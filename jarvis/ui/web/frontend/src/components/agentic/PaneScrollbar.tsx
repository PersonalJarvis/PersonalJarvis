/**
 * A pane's drag scrollbar: a thin overlay on the right edge, driven by the
 * mouse and by nothing else.
 *
 * ## The one hard rule — the wheel is not this component's business
 *
 * A predecessor of this bar is remembered as "it broke scrolling in the
 * terminal". The root cause was elsewhere (a replay buffer dropping the CLI's
 * mouse negotiation), but the lesson stands, so this build makes it a law:
 * nothing here ever calls `preventDefault` on a wheel event, reinterprets one,
 * or stands between the wheel and xterm. The two places wheel events appear
 * at all are (a) a **passive** observer that only counts them, and (b) the
 * track forwarding a clone of any wheel it happens to catch to the terminal's
 * screen, so that scrolling over the bar behaves exactly like scrolling next
 * to it. Everything the bar does itself is done by dragging its thumb or
 * clicking its track — mouse only, by design.
 *
 * ## Why the pane draws its own bar instead of styling the browser's
 *
 * Half the panes have nothing a browser scrollbar could move. A line-printing
 * CLI (Codex, a shell) keeps its history in xterm's scrollback and could use
 * the native bar — but a full-screen CLI (Claude Code) runs on the alternate
 * buffer, where the terminal holds no scrollback at all and the transcript
 * lives inside the application. For those panes the thumb is dragged in
 * relayed wheel notches, and where it stands is COUNTED from the wheel
 * traffic rather than measured from a screen that cannot be read — see
 * ./scrollbarModel for what that claim means and why it re-anchors at the
 * live end. The synthetic notches are dispatched as real wheel events on
 * xterm's screen, because xterm already knows which mouse protocol the
 * application negotiated and encodes the report accordingly; guessing those
 * bytes here would put junk in the CLI's prompt.
 *
 * ## Cost discipline
 *
 * A grid holds a dozen live terminals. While the pointer is elsewhere this
 * component owns two hover listeners and one passive wheel observer, and
 * reads nothing. Terminal state is read only while the bar is on screen, all
 * reads are coalesced into animation frames, and hover is enter/leave rather
 * than per-move tracking.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Terminal } from "@xterm/xterm";
import { cn } from "@/lib/utils";
import type { TerminalAppearance } from "./terminalThemes";
import {
  backAtThumbTop,
  exactView,
  freshTravel,
  hasScroll,
  notchesFor,
  screenTravel,
  SCROLLED_BACK_MARKERS,
  SETTLE_MS,
  thumbBox,
  travelView,
  wheelTravel,
  type ScreenGlance,
  type ScrollView,
  type Travel,
} from "./scrollbarModel";

/** How long the bar lingers after the pointer leaves, so it cannot flicker. */
const LINGER_MS = 200;

/** How long a wheel turn keeps the bar visible as a position readout. */
const FLASH_MS = 800;

/** True when the running application receives wheel events itself. */
function appOwnsScreen(term: Terminal | null): boolean {
  if (!term) return false;
  const tracking = term.modes?.mouseTrackingMode;
  if (Boolean(tracking) && tracking !== "none") return true;
  return term.buffer?.active?.type === "alternate";
}

/**
 * What this pane can scroll right now, or null when nothing is known.
 *
 * Defensive against the bundled xterm's shape on purpose: a missing field
 * must mean "no bar", never an exception inside a render.
 */
export function readScrollView(
  term: Terminal | null,
  travel: Travel,
): ScrollView | null {
  const buffer = term?.buffer?.active;
  const rows = term?.rows ?? 0;
  if (!term || !buffer || rows < 1) return null;
  if (appOwnsScreen(term)) {
    return travelView(travel.travelled, rows, travel.ceiling);
  }
  const view = exactView(
    buffer.length ?? rows,
    buffer.baseY ?? 0,
    buffer.viewportY ?? 0,
    rows,
  );
  return hasScroll(view) ? view : null;
}

/**
 * One look at an application-held pane's screen: is the scrolled-back
 * overlay up, and what does the transcript region above it say?
 *
 * The overlay's row splits the screen — transcript above, the CLI's animated
 * chrome (spinner, input box, status bars) below — so the fingerprint stops
 * there: a spinner repainting every frame must not read as "the transcript
 * moved". Returns null when the buffer cannot be read; the caller keeps its
 * estimate rather than inventing one.
 */
export function readScreen(term: Terminal | null): ScreenGlance | null {
  const buffer = term?.buffer?.active;
  const rows = term?.rows ?? 0;
  if (!term || !buffer || typeof buffer.getLine !== "function" || rows < 1) {
    return null;
  }
  try {
    const text: string[] = [];
    for (let y = 0; y < rows; y += 1) {
      text.push(buffer.getLine(y)?.translateToString(true) ?? "");
    }
    const markerRow = text.findIndex((row) =>
      SCROLLED_BACK_MARKERS.some((marker) => marker.test(row)),
    );
    return {
      markerRow,
      fingerprint: markerRow > 0 ? text.slice(0, markerRow).join("\n") : "",
    };
  } catch {
    return null;
  }
}

/**
 * Hand `count` wheel notches to whatever runs in `host` (direction -1 is
 * towards older output, matching a wheel turned away from the hand).
 */
export function relayNotches(
  host: HTMLElement | null,
  direction: 1 | -1,
  count: number,
): void {
  if (!host || count <= 0 || typeof WheelEvent === "undefined") return;
  const screen = host.querySelector<HTMLElement>(".xterm-screen");
  if (!screen) return;
  const box = screen.getBoundingClientRect();
  for (let i = 0; i < count; i += 1) {
    screen.dispatchEvent(
      new WheelEvent("wheel", {
        bubbles: true,
        cancelable: true,
        clientX: box.left + box.width / 2,
        clientY: box.top + box.height / 2,
        deltaMode: 1,
        deltaY: direction,
      }),
    );
  }
}

interface PaneScrollbarProps {
  /** Pane call-sign — labels and test ids. */
  name: string;
  /**
   * The pane's terminal area, which hover is measured against. It has to be
   * the region the bar sits INSIDE — measured against the xterm host, moving
   * onto the bar itself would already count as having left.
   */
  regionRef: React.RefObject<HTMLElement | null>;
  /** The xterm host, where relayed and forwarded wheel events are aimed. */
  hostRef: React.RefObject<HTMLElement | null>;
  /** The live terminal, or null before the pane has built one. */
  getTerminal: () => Terminal | null;
  /** Bumped whenever the terminal behind `getTerminal` is replaced. */
  epoch: number;
  appearance: TerminalAppearance;
}

export function PaneScrollbar({
  name,
  regionRef,
  hostRef,
  getTerminal,
  epoch,
  appearance,
}: PaneScrollbarProps) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [view, setView] = useState<ScrollView | null>(null);
  const [trackPx, setTrackPx] = useState(0);
  const [hovering, setHovering] = useState(false);
  const [flashing, setFlashing] = useState(false);
  const [dragging, setDragging] = useState(false);
  // The thumb's position while it is held. A full-screen CLI answers relayed
  // notches a beat later, so a thumb drawn only from the counted position
  // would trail the pointer that is holding it.
  const [heldTopPx, setHeldTopPx] = useState<number | null>(null);

  // Where a full-screen CLI's pane stands, counted off the wheel traffic —
  // the user's own turns and this bar's relays alike, since both pass the
  // same observer below — and reconciled against the screen's own anchors
  // (see ./scrollbarModel, "the three anchors").
  const travelRef = useRef<Travel>(freshTravel());
  // Which kind of pane the last reading saw. A CLI that enters or leaves the
  // alternate screen mid-session (a shell running `less`) starts a different
  // history, and a count carried across that line would describe the old one.
  const lastKindRef = useRef<ScrollView["kind"] | null>(null);
  // The sync scheduler, parked here so the always-on wheel observer can poke
  // the read loop that only exists while the bar is visible.
  const scheduleRef = useRef<(() => void) | null>(null);

  const shown = hovering || flashing || dragging;
  const viewRef = useRef(view);
  viewRef.current = view;

  // A replaced terminal is a fresh transcript.
  useEffect(() => {
    travelRef.current = freshTravel();
    lastKindRef.current = null;
    setView(null);
  }, [epoch]);

  // ------------------------------------------------------------------ hover
  // Enter and leave only: a grid of a dozen panes must not pay per pixel of
  // pointer movement to know which one is being reached for.
  useEffect(() => {
    const region = regionRef.current;
    if (!region) return;
    let timer: number | undefined;
    const enter = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = undefined;
      setHovering(true);
    };
    const leave = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = window.setTimeout(() => setHovering(false), LINGER_MS);
    };
    region.addEventListener("mouseenter", enter);
    region.addEventListener("mouseleave", leave);
    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
      region.removeEventListener("mouseenter", enter);
      region.removeEventListener("mouseleave", leave);
    };
  }, [regionRef]);

  // -------------------------------------------------------------- observing
  // The wheel, watched but never touched. Passive so it CANNOT interfere with
  // what xterm does next, capture so it also sees the synthetic notches this
  // bar dispatches, and always on so a pane scrolled a minute ago comes up
  // already knowing how far back it stands.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let timer: number | undefined;
    const onWheel = (event: WheelEvent) => {
      if (event.deltaY === 0) return;
      if (appOwnsScreen(getTerminal())) {
        travelRef.current = wheelTravel(
          travelRef.current,
          event.deltaY,
          Date.now(),
        );
      }
      scheduleRef.current?.();
      setFlashing(true);
      if (timer !== undefined) window.clearTimeout(timer);
      timer = window.setTimeout(() => setFlashing(false), FLASH_MS);
    };
    host.addEventListener("wheel", onWheel, { capture: true, passive: true });
    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
      host.removeEventListener("wheel", onWheel, { capture: true });
    };
  }, [hostRef, getTerminal, epoch]);

  // ---------------------------------------------------------------- reading
  // Terminal state is read only while the bar is on screen, at most once per
  // animation frame — a grid of live agents cannot afford more.
  useEffect(() => {
    if (!shown) {
      scheduleRef.current = null;
      return;
    }
    const term = getTerminal();
    if (!term) return;
    let frame: number | undefined;
    let settleTimer: number | undefined;

    const sync = () => {
      frame = undefined;
      const appHeld = appOwnsScreen(term);
      // Crossing between a terminal-held and an application-held screen
      // resets the count: it described a history that is no longer on stage.
      const kind: ScrollView["kind"] = appHeld ? "travel" : "exact";
      if (lastKindRef.current && kind !== lastKindRef.current) {
        travelRef.current = freshTravel();
      }
      lastKindRef.current = kind;
      if (appHeld) {
        // Reconcile the count with the screen's own anchors — the overlay
        // and the saturation brake (see ./scrollbarModel).
        travelRef.current = screenTravel(
          travelRef.current,
          readScreen(term),
          Date.now(),
        );
        // Unconfirmed ups need a second look once the pty has had its say,
        // even if no event arrives to prompt one.
        if (settleTimer !== undefined) window.clearTimeout(settleTimer);
        if (travelRef.current.pendingUp > 0) {
          settleTimer = window.setTimeout(schedule, SETTLE_MS + 50);
        }
      }
      const next = readScrollView(term, travelRef.current);
      setView((current) => (sameView(current, next) ? current : next));
    };
    const schedule = () => {
      if (frame === undefined) frame = requestAnimationFrame(sync);
    };
    scheduleRef.current = schedule;

    sync();
    const subscriptions = [
      term.onRender?.(schedule),
      term.onResize?.(schedule),
      term.onScroll?.(schedule),
      term.buffer?.onBufferChange?.(schedule),
    ];
    return () => {
      scheduleRef.current = null;
      if (frame !== undefined) cancelAnimationFrame(frame);
      if (settleTimer !== undefined) window.clearTimeout(settleTimer);
      for (const subscription of subscriptions) subscription?.dispose();
    };
  }, [shown, getTerminal, epoch]);

  // -------------------------------------------------------------- geometry
  // Measured outside the render, and only when the box actually changes.
  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;
    const measure = () => {
      setTrackPx((current) =>
        current === track.clientHeight ? current : track.clientHeight,
      );
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(track);
    return () => observer.disconnect();
  }, [view !== null]);

  // ----------------------------------------------------------------- input
  /** Scroll the pane by `lines` towards older output (negative: newer). */
  const scrollBy = useCallback(
    (lines: number) => {
      const term = getTerminal();
      if (!term || lines === 0) return;
      if (appOwnsScreen(term)) {
        // Nothing above the top: while the brake holds, an up-notch would be
        // ignored by the application and only pollute its pty.
        if (lines > 0 && travelRef.current.saturated) return;
        const notches = notchesFor(lines);
        relayNotches(
          hostRef.current,
          notches > 0 ? -1 : 1,
          Math.abs(notches),
        );
        return;
      }
      term.scrollLines?.(-lines);
    },
    [getTerminal, hostRef],
  );

  /** Take the pane to `wanted` lines back from its newest output. */
  const scrollTo = useCallback(
    (wanted: number) => {
      const term = getTerminal();
      if (!term) return;
      if (appOwnsScreen(term)) {
        // Asked in notches, against what has actually been relayed so far —
        // the observer above moves the count as each notch goes past.
        scrollBy(wanted - travelRef.current.travelled);
        return;
      }
      // A terminal that owns its scrollback is told the line outright, so a
      // fast drag cannot overshoot by however far a reading lags the pointer.
      const base = term.buffer?.active?.baseY ?? 0;
      term.scrollToLine?.(Math.max(0, base - Math.max(0, wanted)));
    },
    [getTerminal, scrollBy],
  );

  const onThumbPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const track = trackRef.current;
      if (!track || !getTerminal()) return;
      // A drag on the thumb must not start a text selection in the terminal
      // under it, and must not count as clicking into the pane.
      event.preventDefault();
      event.stopPropagation();

      const height = track.clientHeight;
      // The mapping is frozen at the grab. A travel-counted pane's claimed
      // span grows while it is scrolled back, and a thumb measured against a
      // span that moves underneath slides away from the hand holding it.
      const grabbed = viewRef.current;
      const box = thumbBox(grabbed, height);
      const startTop = box?.topPx ?? 0;
      const startY = event.clientY;
      const travel = Math.max(0, height - (box?.heightPx ?? 0));

      const target = event.currentTarget;
      // Capture keeps the drag alive when the pointer leaves the thin bar —
      // but a build without it (some test environments) still gets a working
      // thumb from the listeners below.
      try {
        target.setPointerCapture(event.pointerId);
      } catch {
        /* no capture here — the drag survives on the listeners alone */
      }
      setDragging(true);
      setHeldTopPx(startTop);

      const onMove = (move: PointerEvent) => {
        if (!Number.isFinite(move.clientY)) return;
        const topPx = Math.min(
          Math.max(startTop + (move.clientY - startY), 0),
          travel,
        );
        setHeldTopPx(topPx);
        scrollTo(backAtThumbTop(topPx, height, grabbed));
      };
      const finish = () => {
        try {
          target.releasePointerCapture(event.pointerId);
        } catch {
          /* never captured */
        }
        target.removeEventListener("pointermove", onMove);
        target.removeEventListener("pointerup", finish);
        target.removeEventListener("pointercancel", finish);
        setDragging(false);
        setHeldTopPx(null);
      };
      target.addEventListener("pointermove", onMove);
      target.addEventListener("pointerup", finish);
      target.addEventListener("pointercancel", finish);
    },
    [getTerminal, scrollTo],
  );

  // A press on the empty track pages towards the pointer, like every other
  // scrollbar.
  const onTrackPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const track = trackRef.current;
      const current = viewRef.current;
      if (!track || !current) return;
      event.preventDefault();
      event.stopPropagation();
      const box = track.getBoundingClientRect();
      const geometry = thumbBox(current, box.height);
      if (!geometry) return;
      const page = Math.max(1, current.rows - 1);
      const towardsNewer = event.clientY - box.top > geometry.topPx;
      scrollBy(towardsNewer ? -page : page);
    },
    [scrollBy],
  );

  // Wheel over the bar: forwarded to the terminal untouched, so the strip of
  // pixels the bar occupies scrolls exactly like every other part of the
  // pane. The bar itself has no wheel behaviour at all.
  const onTrackWheel = useCallback(
    (event: React.WheelEvent<HTMLDivElement>) => {
      const screen =
        hostRef.current?.querySelector<HTMLElement>(".xterm-screen");
      if (!screen || typeof WheelEvent === "undefined") return;
      screen.dispatchEvent(
        new WheelEvent("wheel", {
          bubbles: true,
          cancelable: true,
          clientX: event.clientX,
          clientY: event.clientY,
          deltaMode: event.deltaMode,
          deltaY: event.deltaY,
          deltaX: event.deltaX,
        }),
      );
    },
    [hostRef],
  );

  // ---------------------------------------------------------------- render
  const box = useMemo(() => thumbBox(view, trackPx), [view, trackPx]);
  if (!hasScroll(view)) return null;

  const light = appearance === "light";

  return (
    <div
      ref={trackRef}
      role="scrollbar"
      aria-orientation="vertical"
      aria-label={`Scroll ${name}`}
      aria-valuemin={0}
      aria-valuemax={view?.above ?? 0}
      aria-valuenow={(view?.above ?? 0) - (view?.back ?? 0)}
      data-testid={`pane-scrollbar-${name}`}
      data-shown={shown ? "true" : "false"}
      data-kind={view?.kind}
      onPointerDown={onTrackPointerDown}
      onWheel={onTrackWheel}
      onMouseDown={(event) => event.stopPropagation()}
      className={cn(
        "absolute bottom-1 top-1 z-10 w-[9px] rounded-full transition-opacity duration-150",
        shown ? "opacity-100" : "pointer-events-none opacity-0",
      )}
      style={{
        right: 2,
        background: shown
          ? light
            ? "rgba(0,0,0,0.06)"
            : "rgba(255,255,255,0.07)"
          : "transparent",
      }}
    >
      {box && (
        <div
          data-testid={`pane-scrollbar-thumb-${name}`}
          onPointerDown={onThumbPointerDown}
          className={cn(
            "absolute left-0 w-full rounded-full",
            dragging ? "cursor-grabbing" : "cursor-grab transition-[top]",
          )}
          style={{
            top: heldTopPx ?? box.topPx,
            height: box.heightPx,
            background: `rgb(var(--jarvis-yellow) / ${dragging ? 0.95 : 0.6})`,
            transitionDuration: dragging ? undefined : "80ms",
          }}
        />
      )}
    </div>
  );
}

function sameView(a: ScrollView | null, b: ScrollView | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return (
    a.kind === b.kind &&
    a.above === b.above &&
    a.back === b.back &&
    a.rows === b.rows
  );
}
