/**
 * A pane's scrollbar: there whenever the pointer is on the pane, gone when it
 * is not, and able to scroll whatever the pane happens to be running.
 *
 * ## Why the pane draws its own instead of styling the browser's
 *
 * Because half the panes have nothing for a browser scrollbar to move. A CLI
 * that prints line by line (Codex, a shell) leaves its transcript in the
 * terminal's scrollback, and `.xterm-viewport` scrolls it. A CLI that takes the
 * whole screen keeps its transcript to itself — measured against Claude Code
 * 2.1.220, it switches to the alternate buffer and asks for full SGR mouse
 * tracking within milliseconds of starting, after which the terminal holds no
 * scrollback at all and the wheel is forwarded to the application. There is
 * nothing there to style.
 *
 * So the bar is drawn here and scrolls whichever of the two it is on:
 *
 * * the terminal's viewport, by line, when the terminal owns the history;
 * * the application itself, by relayed wheel notches, when it does not.
 *
 * ## It shows up on hover, and it does not ask permission first
 *
 * Reaching a pane makes its bar appear — no measurement, no probe, no proving
 * that there is a history worth drawing. That rule is the whole point of this
 * rewrite: the version before it demanded evidence from a CLI that cannot give
 * any, and every failure to get that evidence looked exactly like "nothing to
 * scroll here", so the bar never appeared in the panes it was written for. See
 * ./paneScrollModel for what an application-held pane claims instead.
 *
 * A pane with genuinely nothing to scroll — a shell that has printed four lines
 * — still gets no bar, because its terminal says so exactly.
 *
 * ## And it stays off the frame budget
 *
 * A grid can hold a dozen live terminals. So: nothing is read from a terminal
 * while the bar is invisible, everything read while it is visible is coalesced
 * into one animation frame, the track is measured by a ResizeObserver rather
 * than during render, and hover is two events (enter, leave) rather than one
 * per pointer move.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Terminal } from "@xterm/xterm";
import { cn } from "@/lib/utils";
import type { TerminalAppearance } from "./terminalThemes";
import {
  appScroll,
  backForThumbTop,
  bufferScroll,
  LINES_PER_NOTCH,
  notchesForLines,
  scrollable,
  thumbGeometry,
  type PaneScroll,
} from "./paneScrollModel";

/** How long the bar lingers after the pointer leaves, so it cannot flicker. */
const LINGER_MS = 200;

/** How long a wheel turn keeps the bar up when the pointer is elsewhere. */
const FLASH_MS = 700;

/**
 * Where the bar sits before the pane has been measured.
 *
 * xterm reserves a strip on the right for the scrollbar it believes it has, and
 * the bar belongs inside that strip rather than floating in the pane's padding.
 * The real distance is measured below; this is only the first paint.
 */
const INSET_FALLBACK_PX = 6;

/** True when the running application receives wheel events itself. */
function appTakesWheel(term: Terminal | null): boolean {
  const tracking = term?.modes?.mouseTrackingMode;
  return Boolean(tracking) && tracking !== "none";
}

/**
 * What this pane can scroll right now.
 *
 * `back` is what the component has counted for an application-held pane; it is
 * ignored for a terminal that can answer the question itself.
 *
 * Defensive by design — this runs against whatever xterm the app was bundled
 * with, and a missing field must mean "no bar" rather than an exception thrown
 * inside a render.
 */
export function readPaneScroll(
  term: Terminal | null,
  back: number,
): PaneScroll | null {
  const buffer = term?.buffer?.active;
  const rows = term?.rows ?? 0;
  if (!term || !buffer || rows < 1) return null;

  // The application holds the history in EITHER buffer once it has taken the
  // mouse, and in the alternate buffer there is no scrollback either way.
  if (appTakesWheel(term) || buffer.type === "alternate") {
    return appScroll(back, rows);
  }

  const scroll = bufferScroll(
    buffer.length ?? rows,
    buffer.baseY ?? 0,
    buffer.viewportY ?? 0,
    rows,
  );
  return scrollable(scroll) ? scroll : null;
}

/**
 * Hand one wheel notch to whatever is running in `host`.
 *
 * Dispatched as a real wheel event on xterm's screen rather than written to the
 * socket by hand: xterm already knows which mouse protocol the application
 * negotiated and encodes the report accordingly, and for a full-screen
 * application that did NOT take the mouse it sends the cursor keys a real wheel
 * turn would send. Guessing those bytes here would put junk in the CLI's prompt.
 *
 * Line mode with a delta of exactly one, which is what a notch is.
 */
export function relayWheelNotch(
  host: HTMLElement | null,
  direction: 1 | -1,
): boolean {
  if (!host || typeof WheelEvent === "undefined") return false;
  const screen = host.querySelector<HTMLElement>(".xterm-screen");
  if (!screen) return false;
  const box = screen.getBoundingClientRect();
  return screen.dispatchEvent(
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

interface PaneScrollbarProps {
  /** Pane call-sign — used for labels and test ids. */
  name: string;
  /**
   * The pane's terminal area. Hover is measured against THIS element, because
   * the bar sits inside it: with the xterm host as the reference, moving onto
   * the bar would count as leaving the pane.
   */
  regionRef: React.RefObject<HTMLElement | null>;
  /** The xterm host, where a relayed wheel event is dispatched. */
  hostRef: React.RefObject<HTMLElement | null>;
  /** The live terminal, or null before it is built. */
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
  const [scroll, setScroll] = useState<PaneScroll | null>(null);
  const [trackPx, setTrackPx] = useState(0);
  const [inset, setInset] = useState(INSET_FALLBACK_PX);
  const [hovering, setHovering] = useState(false);
  const [flashing, setFlashing] = useState(false);
  const [dragging, setDragging] = useState(false);
  // Where the thumb is while it is held. An application answers a relayed
  // notch a moment later, so a thumb drawn only from what has been counted
  // would lag behind the pointer holding it.
  const [dragTopPx, setDragTopPx] = useState<number | null>(null);

  // How far an application-held pane stands back from its newest output, in
  // lines. Counted from the wheel traffic the pane sees — the user's own turns
  // and the bar's relays alike, since both scroll the same application.
  const backRef = useRef(0);
  const [back, setBack] = useState(0);

  const shown = hovering || flashing || dragging;
  // The track is in the document only while there is something to scroll, so
  // anything that measures it has to run again when it appears.
  const hasBar = scrollable(scroll);
  const scrollRef = useRef(scroll);
  scrollRef.current = scroll;

  // A fresh terminal is a fresh transcript.
  useEffect(() => {
    backRef.current = 0;
    setBack(0);
    setScroll(null);
  }, [epoch]);

  // ------------------------------------------------------------------ hover
  // Enter and leave, not every pointer move: a grid of a dozen panes must not
  // pay per pixel to know which one the pointer is on.
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

  // ------------------------------------------------------- follow the wheel
  // Runs whether the bar is visible or not, and costs nothing until a wheel
  // actually turns: a pane scrolled a minute ago must come up knowing where it
  // stands. Capture, so it sees the turn before xterm forwards it, and passive,
  // so it can never interfere with what xterm does next.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let timer: number | undefined;

    const onWheel = (event: WheelEvent) => {
      if (event.deltaY === 0) return;
      const term = getTerminal();
      if (appTakesWheel(term) || term?.buffer?.active?.type === "alternate") {
        const next = Math.max(
          0,
          backRef.current +
            (event.deltaY > 0 ? -LINES_PER_NOTCH : LINES_PER_NOTCH),
        );
        backRef.current = next;
        setBack(next);
      }
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
  // Only while the bar is on screen, and never more than once a frame: reading
  // a terminal's buffer on every render of every pane is exactly the kind of
  // work a grid of live agents cannot afford.
  useEffect(() => {
    if (!shown) return;
    const term = getTerminal();
    if (!term) return;
    let frame: number | undefined;

    const sync = () => {
      frame = undefined;
      const next = readPaneScroll(term, backRef.current);
      setScroll((current) => (same(current, next) ? current : next));
    };
    const schedule = () => {
      if (frame === undefined) frame = requestAnimationFrame(sync);
    };

    sync();
    const subscriptions = [
      term.onRender?.(schedule),
      term.onResize?.(schedule),
      term.onScroll?.(schedule),
      term.buffer?.onBufferChange?.(schedule),
    ];
    return () => {
      if (frame !== undefined) cancelAnimationFrame(frame);
      for (const subscription of subscriptions) subscription?.dispose();
    };
  }, [shown, getTerminal, epoch]);

  // A counted step moves the thumb even on a pane that repaints nothing.
  useEffect(() => {
    if (!shown) return;
    const term = getTerminal();
    if (!term) return;
    const next = readPaneScroll(term, back);
    setScroll((current) => (same(current, next) ? current : next));
  }, [back, shown, getTerminal, epoch]);

  // ------------------------------------------------------------- geometry
  // Measured from outside the render, and only when a box actually changes.
  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;

    const measure = () => {
      setTrackPx((current) =>
        current === track.clientHeight ? current : track.clientHeight,
      );
      const region = regionRef.current;
      const host = hostRef.current;
      if (!region || !host) return;
      const gap = Math.max(
        0,
        Math.round(
          region.getBoundingClientRect().right -
            host.getBoundingClientRect().right,
        ),
      );
      setInset((current) => (current === gap ? current : gap));
    };

    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(track);
    if (regionRef.current) observer.observe(regionRef.current);
    if (hostRef.current) observer.observe(hostRef.current);
    return () => observer.disconnect();
  }, [hasBar, regionRef, hostRef]);

  // ------------------------------------------------------------------ input
  /** Does this pane's application hold the screen, rather than the terminal? */
  const appHeld = useCallback(() => {
    const term = getTerminal();
    return Boolean(
      term &&
      (appTakesWheel(term) || term.buffer?.active?.type === "alternate"),
    );
  }, [getTerminal]);

  /** Scroll the pane by `lines` towards older output (negative: newer). */
  const scrollBy = useCallback(
    (lines: number) => {
      const term = getTerminal();
      if (!term || lines === 0) return;
      if (appHeld()) {
        const notches = notchesForLines(lines);
        for (let i = 0; i < Math.abs(notches); i += 1) {
          relayWheelNotch(hostRef.current, notches > 0 ? -1 : 1);
        }
        return;
      }
      term.scrollLines?.(-lines);
    },
    [getTerminal, hostRef, appHeld],
  );

  /**
   * Take the pane to `wanted` lines back from its newest output.
   *
   * A terminal that owns its scrollback is told the line outright, so a fast
   * drag cannot overshoot by however far the reading lags behind the pointer.
   * An application can only be asked in notches, and is: the difference is
   * counted against `backRef`, which our own relay updates as it dispatches.
   */
  const scrollTo = useCallback(
    (wanted: number) => {
      const term = getTerminal();
      if (!term) return;
      if (appHeld()) {
        scrollBy(wanted - backRef.current);
        return;
      }
      const base = term.buffer?.active?.baseY ?? 0;
      term.scrollToLine?.(Math.max(0, base - Math.max(0, wanted)));
    },
    [getTerminal, appHeld, scrollBy],
  );

  const onThumbPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const track = trackRef.current;
      const term = getTerminal();
      if (!track || !term) return;
      event.preventDefault();
      event.stopPropagation();

      const height = track.clientHeight;
      const start = scrollRef.current;
      const geometry = thumbGeometry(start, height);
      const startTop = geometry?.topPx ?? 0;
      const startY = event.clientY;
      const travel = Math.max(0, height - (geometry?.heightPx ?? 0));

      const target = event.currentTarget;
      // Capture keeps the drag alive when the pointer runs off the thin bar,
      // but it is not worth losing the drag over: engines and test
      // environments that do not implement it still get a working thumb.
      try {
        target.setPointerCapture(event.pointerId);
      } catch {
        /* no capture — the listeners below carry the drag anyway */
      }
      setDragging(true);
      setDragTopPx(startTop);

      // The mapping is frozen at the grab. An application-held pane's assumed
      // span grows as it is scrolled back (see ./paneScrollModel), and a thumb
      // measured against a span that moves under the pointer slides away from
      // the hand holding it.
      const onMove = (move: PointerEvent) => {
        if (!Number.isFinite(move.clientY)) return;
        const topPx = Math.min(
          Math.max(startTop + (move.clientY - startY), 0),
          travel,
        );
        setDragTopPx(topPx);
        scrollTo(backForThumbTop(topPx, height, start));
      };
      const finish = () => {
        target.releasePointerCapture?.(event.pointerId);
        target.removeEventListener("pointermove", onMove);
        target.removeEventListener("pointerup", finish);
        target.removeEventListener("pointercancel", finish);
        setDragging(false);
        setDragTopPx(null);
      };

      target.addEventListener("pointermove", onMove);
      target.addEventListener("pointerup", finish);
      target.addEventListener("pointercancel", finish);
    },
    [getTerminal, scrollBy],
  );

  // A click on the empty part of the track pages towards it, as every other
  // scrollbar does.
  const onTrackPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const track = trackRef.current;
      const current = scrollRef.current;
      if (!track || !current) return;
      event.preventDefault();
      event.stopPropagation();
      const box = track.getBoundingClientRect();
      const geometry = thumbGeometry(current, box.height);
      if (!geometry) return;
      const page = Math.max(1, current.rows - 1);
      const towardsNewer = event.clientY - box.top > geometry.topPx;
      scrollBy(towardsNewer ? -page : page);
    },
    [scrollBy],
  );

  const onWheel = useCallback(
    (event: React.WheelEvent<HTMLDivElement>) => {
      scrollBy(event.deltaY > 0 ? -LINES_PER_NOTCH : LINES_PER_NOTCH);
    },
    [scrollBy],
  );

  // ----------------------------------------------------------------- render
  const geometry = useMemo(
    () => thumbGeometry(scroll, trackPx),
    [scroll, trackPx],
  );
  if (!hasBar) return null;

  const light = appearance === "light";
  const strength = dragging ? 0.95 : 0.62;

  return (
    <div
      ref={trackRef}
      role="scrollbar"
      aria-orientation="vertical"
      aria-label={`Scroll ${name}`}
      aria-valuemin={0}
      aria-valuemax={scroll?.span ?? 0}
      aria-valuenow={(scroll?.span ?? 0) - (scroll?.back ?? 0)}
      data-testid={`pane-scrollbar-${name}`}
      data-shown={shown ? "true" : "false"}
      onPointerDown={onTrackPointerDown}
      onWheel={onWheel}
      onMouseDown={(event) => event.stopPropagation()}
      className={cn(
        "absolute bottom-1 top-1 z-10 w-[10px] rounded-full transition-opacity duration-150",
        shown ? "opacity-100" : "pointer-events-none opacity-0",
      )}
      style={{
        right: inset,
        background: shown
          ? light
            ? "rgba(0,0,0,0.06)"
            : "rgba(255,255,255,0.07)"
          : "transparent",
      }}
    >
      {geometry && (
        <div
          data-testid={`pane-scrollbar-thumb-${name}`}
          onPointerDown={onThumbPointerDown}
          className={cn(
            "absolute left-0 w-full rounded-full",
            dragging ? "cursor-grabbing" : "cursor-grab transition-[top]",
          )}
          style={{
            top: dragTopPx ?? geometry.topPx,
            height: geometry.heightPx,
            background: `rgb(var(--jarvis-yellow) / ${strength})`,
            transitionDuration: dragging ? undefined : "80ms",
          }}
        />
      )}
    </div>
  );
}

function same(a: PaneScroll | null, b: PaneScroll | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return a.span === b.span && a.back === b.back && a.rows === b.rows;
}
