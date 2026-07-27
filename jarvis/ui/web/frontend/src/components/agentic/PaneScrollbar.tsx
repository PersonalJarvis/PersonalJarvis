/**
 * A pane's own scrollbar — hidden until you reach for it.
 *
 * Replaces the native `.xterm-viewport` bar, for two reasons the CSS version
 * could not solve:
 *
 * 1. **It was always there.** `overflow-y: scroll` draws a permanent track down
 *    the side of every pane, and in a grid of eight terminals that is eight
 *    stripes of furniture nobody asked for. This one appears when the pointer
 *    comes within reach of the pane's right edge, and while you are scrolled
 *    back through history — and is otherwise invisible.
 * 2. **It only worked for half the CLIs.** Why, and what this does about it,
 *    is in ./paneScroll and ./paneAppScroll — in short: Claude Code runs on the
 *    alternate screen with mouse tracking, so the terminal holds no scrollback
 *    to read a position from and the wheel belongs to the CLI. That pane's
 *    position is measured from how its screen moves instead, and its thumb is
 *    drawn from that measurement like any other.
 *
 * The bar is an overlay, not a layout box: xterm reserves a gutter on the right
 * for the scrollbar it thinks it has, and the bar sits in that gutter, so
 * nothing is drawn underneath it.
 *
 * That gutter is real even though ./index.css gives the native bar zero width.
 * Measured against xterm 5.5.0, `Viewport` computes
 * `scrollBarWidth = viewportElement.offsetWidth - scrollArea.offsetWidth || 15`
 * — and a scrollbar hidden by CSS makes that difference 0, so the `|| 15`
 * fallback takes over and `FitAddon.proposeDimensions` drops 15 px of columns.
 * Hence `hostGap`: the bar is placed flush against the terminal host's right
 * edge, INSIDE the reserved strip. Floating it in the pane's own padding instead
 * (the original `right-1`) left dead space on both sides of it — part of the
 * empty right-hand strip reported on 2026-07-27.
 *
 * And reaching for it is what MAKES it appear in an agent pane: the position of a
 * CLI that holds its own history has to be measured, and until this pane had been
 * scrolled there was nothing measured to draw. See `probeAppHistory` in
 * ./paneAppScroll — the gesture asks the question now.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { Terminal } from "@xterm/xterm";
import { cn } from "@/lib/utils";
import type { TerminalAppearance } from "./terminalThemes";
import {
  IDLE_STATE,
  lineForThumbTop,
  readScrollState,
  relayWheelNotch,
  thumbGeometry,
  type PaneScrollState,
} from "./paneScroll";
import {
  appTakesWheel,
  AT_LIVE_END,
  hasMeasuredHistory,
  measuredNoHistory,
  notchesForLines,
  probeAppHistory,
  PROBE_STALE_MS,
  PROBE_WAIT_MS,
  trackAppScroll,
  type AppScrollPosition,
} from "./paneAppScroll";

/** How close to the pane's right edge the pointer reveals the bar. */
const HOT_ZONE_PX = 28;

/**
 * Where the bar sits until the real distance has been measured.
 *
 * It belongs flush against the xterm host's right edge — the near side of the
 * gutter xterm reserves for the scrollbar it thinks it has (see the file header).
 * That distance is the terminal region's own horizontal padding, and it is
 * MEASURED (`hostGap` below) rather than restated here: this constant used to
 * name the padding as a number, the padding was then changed in ./AgenticTerminal
 * without it, and the bar spent 2 px sitting on top of the agent's output. A
 * layout constant duplicated across two files drifts the moment either one is
 * touched. Used only for the very first paint, before the measurement lands.
 */
const BAR_INSET_FALLBACK_PX = 6;

/** Grace period before a bar the pointer left disappears again. */
const HIDE_DELAY_MS = 260;

/** How long the bar stays up after scrolling back through history. */
const FLASH_MS = 900;

function sameState(a: PaneScrollState, b: PaneScrollState): boolean {
  return (
    a.mode === b.mode &&
    a.total === b.total &&
    a.rows === b.rows &&
    a.top === b.top
  );
}

interface PaneScrollbarProps {
  /** Pane call-sign — used for labels and test ids. */
  name: string;
  /**
   * The pane's terminal area. Hover is measured against THIS element rather
   * than the xterm host, because the bar itself sits inside it: with the host
   * as the reference, moving onto the bar would count as leaving the pane and
   * the bar would vanish under the pointer.
   */
  regionRef: React.RefObject<HTMLElement | null>;
  /** The xterm host, where a relayed wheel event is dispatched. */
  hostRef: React.RefObject<HTMLElement | null>;
  /** The live terminal, or null before it is built. */
  getTerminal: () => Terminal | null;
  /**
   * Bumped whenever the terminal behind `getTerminal` is replaced, so the
   * subscriptions below are torn down and rebuilt against the new instance.
   */
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
  const [state, setState] = useState<PaneScrollState>(IDLE_STATE);
  const [trackPx, setTrackPx] = useState(0);
  // The gap between the pane's right edge and the xterm host's — measured, not
  // restated from ./AgenticTerminal. See `BAR_INSET_FALLBACK_PX`.
  const [hostGap, setHostGap] = useState(BAR_INSET_FALLBACK_PX);
  const [nearEdge, setNearEdge] = useState(false);
  const [flashing, setFlashing] = useState(false);
  const [dragging, setDragging] = useState(false);
  // Where the thumb is while it is held. The application answers a relayed
  // notch a network round trip later, so a thumb drawn only from the
  // measurement would lag behind the pointer holding it.
  const [dragTopPx, setDragTopPx] = useState<number | null>(null);
  // What ./paneAppScroll has measured of a CLI that holds its own history.
  const [position, setPosition] = useState<AppScrollPosition>(AT_LIVE_END);

  const shown = nearEdge || flashing || dragging;
  const stateRef = useRef(state);
  stateRef.current = state;
  const positionRef = useRef(position);
  positionRef.current = position;

  // ------------------------------------------------------------------ hover
  useEffect(() => {
    const region = regionRef.current;
    if (!region) return;
    let hideTimer: number | undefined;

    const clearHide = () => {
      if (hideTimer !== undefined) window.clearTimeout(hideTimer);
      hideTimer = undefined;
    };
    const leave = () => {
      clearHide();
      hideTimer = window.setTimeout(() => setNearEdge(false), HIDE_DELAY_MS);
    };
    const onMove = (event: MouseEvent) => {
      const rect = region.getBoundingClientRect();
      const inside =
        event.clientY >= rect.top &&
        event.clientY <= rect.bottom &&
        rect.right - event.clientX <= HOT_ZONE_PX &&
        event.clientX <= rect.right;
      if (inside) {
        clearHide();
        setNearEdge(true);
      } else {
        leave();
      }
    };

    region.addEventListener("mousemove", onMove);
    region.addEventListener("mouseleave", leave);
    return () => {
      clearHide();
      region.removeEventListener("mousemove", onMove);
      region.removeEventListener("mouseleave", leave);
    };
  }, [regionRef]);

  // -------------------------------------------------- measure an app's scroll
  // Runs whether the bar is visible or not, and costs nothing until a wheel
  // actually turns: a bar that only started measuring when it appeared would
  // come up knowing nothing about a pane the user scrolled a minute ago.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    return trackAppScroll({ host, getTerminal, onChange: setPosition });
  }, [hostRef, getTerminal, epoch]);

  // A fresh terminal is a fresh transcript — carrying a measured position over
  // a restart would put the thumb somewhere in a history that no longer exists.
  useEffect(() => {
    setPosition(AT_LIVE_END);
  }, [epoch]);

  // ------------------------------------------------ measure on first reach
  // Reaching for the bar is itself the request for one, so a pane that has
  // nothing measured yet gets asked — see `probeAppHistory` in ./paneAppScroll
  // for why waiting for a wheel turn instead left the bar unreachable in exactly
  // the panes it exists for.
  //
  // Asked only while nothing is known, and only after the pane has been quiet for
  // `PROBE_WAIT_MS`: a probe moves the application by a notch and back, which is
  // invisible once but would be a twitch on every pass of the pointer — and a
  // probe folded into a burst of the user's own scrolling measures neither of
  // them. `probedAt` rather than the position alone, because the answer lands a
  // settle period later and a pointer crossing the hot zone twice in that time
  // must not ask twice.
  //
  // Only one answer stands for good: "there IS history here". Everything else
  // expires. "This pane has no history at all" is true of the moment it was
  // measured, and a pane that was empty when it opened fills up; a probe that
  // produced no answer AT ALL — sent while the application was repainting, or
  // into a terminal that was still being built — leaves the pane in the very
  // same state, without a bar and with nothing measured. Treating only the
  // first of those as re-askable is what left a pane one unlucky probe away
  // from having no scrollbar for the rest of its life. See `measuredNoHistory`
  // and `PROBE_STALE_MS` in ./paneAppScroll.
  const probedAt = useRef<{ epoch: number; at: number } | null>(null);
  useEffect(() => {
    if (!nearEdge) return;
    const host = hostRef.current;
    const term = getTerminal();
    if (!host || !term || !appTakesWheel(term)) return;

    const stale = () => {
      const last = probedAt.current;
      if (!last || last.epoch !== epoch) return true;
      return Date.now() - last.at >= PROBE_STALE_MS;
    };
    // Nothing to ask while a history is known to exist — except when the only
    // thing known is that there was none, and that reading has gone stale.
    const settled = () =>
      hasMeasuredHistory(positionRef.current) &&
      !measuredNoHistory(positionRef.current);
    if (settled() || !stale()) return;

    let stopProbe: (() => void) | undefined;
    let wait: number | undefined;

    const ask = () => {
      wait = undefined;
      host.removeEventListener("wheel", defer, { capture: true });
      // The wait may have been all it took: a wheel turn measures the pane
      // better than any probe, so if one landed there is nothing left to ask.
      if (settled()) return;
      probedAt.current = { epoch, at: Date.now() };
      stopProbe = probeAppHistory((direction) =>
        relayWheelNotch(host, direction),
      );
    };
    // A wheel turn while the probe is still pending pushes it back, so the
    // tracker never has two sources of movement inside one burst.
    function defer(event: WheelEvent) {
      if (event.deltaY === 0) return;
      if (wait !== undefined) window.clearTimeout(wait);
      wait = window.setTimeout(ask, PROBE_WAIT_MS);
    }

    host.addEventListener("wheel", defer, { capture: true, passive: true });
    wait = window.setTimeout(ask, PROBE_WAIT_MS);
    return () => {
      if (wait !== undefined) window.clearTimeout(wait);
      host.removeEventListener("wheel", defer, { capture: true });
      stopProbe?.();
    };
  }, [nearEdge, getTerminal, epoch, hostRef]);

  // ------------------------------------------------- flash while scrolled back
  // Deliberately NOT "flash on any scroll": a pane pinned to the bottom scrolls
  // on every line an agent prints, and a bar that lit up for each of them would
  // be the permanent stripe this replaces. Only a viewport sitting ABOVE the
  // live end means somebody is reading history.
  useEffect(() => {
    const term = getTerminal();
    if (!term?.onScroll) return;
    let timer: number | undefined;
    const subscription = term.onScroll(() => {
      const buffer = term.buffer?.active;
      if (!buffer || buffer.viewportY >= buffer.baseY) return;
      setFlashing(true);
      if (timer !== undefined) window.clearTimeout(timer);
      timer = window.setTimeout(() => setFlashing(false), FLASH_MS);
    });
    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
      subscription.dispose();
    };
  }, [getTerminal, epoch]);

  // The same flash for an app-mode pane, which produces no xterm scroll event
  // at all: the only sign that somebody is reading history is the measurement
  // moving away from the live end. The timer is held in a ref rather than
  // cleared per effect run, because a measurement that lands back AT the live
  // end must let the flash run out — not cancel it and leave the bar up.
  const flashTimer = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (position.offset === 0) return;
    setFlashing(true);
    if (flashTimer.current !== undefined)
      window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlashing(false), FLASH_MS);
  }, [position]);
  useEffect(
    () => () => {
      if (flashTimer.current !== undefined) {
        window.clearTimeout(flashTimer.current);
      }
    },
    [],
  );

  // ---------------------------------------------------------------- metrics
  // Only while the bar is on screen. A hidden bar needs no numbers, and reading
  // them on every frame of a busy pane is exactly the kind of work the grid
  // spends its frame budget avoiding.
  useEffect(() => {
    if (!shown) return;
    const term = getTerminal();
    if (!term) return;

    let frame: number | undefined;
    const sync = () => {
      frame = undefined;
      const next = readScrollState(term, positionRef.current);
      if (!sameState(next, stateRef.current)) setState(next);
      const track = trackRef.current;
      if (track) {
        const height = track.clientHeight;
        setTrackPx((current) => (current === height ? current : height));
      }
    };
    const schedule = () => {
      if (frame === undefined) frame = requestAnimationFrame(sync);
    };

    sync();
    const subscriptions = [
      term.onRender?.(schedule),
      term.onResize?.(schedule),
      term.buffer?.onBufferChange?.(schedule),
    ];
    return () => {
      if (frame !== undefined) cancelAnimationFrame(frame);
      for (const subscription of subscriptions) subscription?.dispose();
    };
  }, [shown, getTerminal, epoch]);

  // A new measurement moves the thumb even on a pane that renders nothing —
  // an app scrolled back through its own history repaints once and then sits
  // still, and the effect above would never hear about it. Only once something
  // HAS been measured, so an untouched pane keeps the bar out of the document
  // entirely rather than mounting it invisible.
  useEffect(() => {
    const term = getTerminal();
    if (!term || position === AT_LIVE_END) return;
    const next = readScrollState(term, position);
    setState((current) => (sameState(current, next) ? current : next));
  }, [position, getTerminal, epoch]);

  /*
   * The track's height decides the thumb's, and the bar's distance from the
   * pane edge decides where it sits. Both are geometry, so both are MEASURED —
   * and both are measured when the boxes actually change rather than after
   * every render.
   *
   * That distinction is the whole point of this effect. It used to run on every
   * commit, and reading a box in a commit forces the browser to recompute the
   * layout there and then — for each pane, on a thread that has just resized
   * all of them. With a dozen panes and a seam being dragged, those forced
   * recomputations were the single most expensive thing in the frame, and the
   * measured height changing then fed a second render per pane on top. A
   * ResizeObserver answers the same question from outside the render, only when
   * there is a new answer.
   *
   * `state.mode` is in the dependencies because the track is in the document
   * only while there is something to scroll — the observer has to be attached
   * to the element that exists now.
   */
  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;

    const measure = () => {
      const height = track.clientHeight;
      setTrackPx((current) => (current === height ? current : height));

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
      setHostGap((current) => (current === gap ? current : gap));
    };

    measure();
    // No observer in this environment (older engines, some test setups): the
    // one measurement above still gives the bar a thumb, which is the
    // behaviour that matters.
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(track);
    if (regionRef.current) observer.observe(regionRef.current);
    if (hostRef.current) observer.observe(hostRef.current);
    return () => observer.disconnect();
  }, [state.mode, regionRef, hostRef]);

  // ------------------------------------------------------------------ input
  const relay = useCallback(
    (notches: number) => {
      const direction = notches > 0 ? 1 : -1;
      for (let i = 0; i < Math.abs(notches); i += 1) {
        relayWheelNotch(hostRef.current, direction);
      }
    },
    [hostRef],
  );

  const onThumbPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const term = getTerminal();
      const track = trackRef.current;
      if (!term || !track) return;
      event.preventDefault();
      event.stopPropagation();

      const current = stateRef.current;
      const height = track.clientHeight;
      const startY = event.clientY;
      const geometry = thumbGeometry(current, height);
      const startTop = geometry?.topPx ?? 0;
      const travel = Math.max(0, height - (geometry?.heightPx ?? 0));
      // Where the drag began in the history, and how many notches have been
      // relayed towards its target so far. Counted against the START rather
      // than against the live measurement, so a reading that lands mid-drag
      // cannot make the same notch be sent twice.
      const startLine = current.top;
      const step = positionRef.current.linesPerNotch;
      let sentNotches = 0;

      const target = event.currentTarget;
      target.setPointerCapture(event.pointerId);
      setDragging(true);
      setDragTopPx(startTop);

      const onMove = (move: PointerEvent) => {
        const topPx = Math.min(
          Math.max(startTop + (move.clientY - startY), 0),
          travel,
        );
        setDragTopPx(topPx);
        const line = lineForThumbTop(topPx, height, current);
        if (current.mode === "app") {
          const wanted = notchesForLines(line - startLine, step);
          if (wanted !== sentNotches) {
            relay(wanted - sentNotches);
            sentNotches = wanted;
          }
          return;
        }
        term.scrollToLine?.(line);
      };
      const onUp = () => {
        target.releasePointerCapture?.(event.pointerId);
        target.removeEventListener("pointermove", onMove);
        target.removeEventListener("pointerup", onUp);
        target.removeEventListener("pointercancel", onUp);
        setDragging(false);
        setDragTopPx(null);
      };

      target.addEventListener("pointermove", onMove);
      target.addEventListener("pointerup", onUp);
      target.addEventListener("pointercancel", onUp);
    },
    [getTerminal, relay],
  );

  // A click on the empty part of the track pages towards it, the way every
  // other scrollbar does.
  const onTrackPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const term = getTerminal();
      const track = trackRef.current;
      if (!term || !track) return;
      event.preventDefault();
      event.stopPropagation();
      const current = stateRef.current;
      const rect = track.getBoundingClientRect();
      const geometry = thumbGeometry(current, rect.height);
      if (!geometry) return;
      const page = Math.max(1, current.rows - 1);
      const down = event.clientY - rect.top > geometry.topPx;
      if (current.mode === "app") {
        relay(
          notchesForLines(
            down ? page : -page,
            positionRef.current.linesPerNotch,
          ),
        );
      } else {
        term.scrollLines?.(down ? page : -page);
      }
    },
    [getTerminal, relay],
  );

  const onWheel = useCallback(
    (event: React.WheelEvent<HTMLDivElement>) => {
      const term = getTerminal();
      if (!term) return;
      const current = stateRef.current;
      const notches = event.deltaY > 0 ? 1 : -1;
      if (current.mode === "app") relay(notches * 3);
      else term.scrollLines?.(notches * 3);
    },
    [getTerminal, relay],
  );

  // ----------------------------------------------------------------- render
  const geometry = thumbGeometry(state, trackPx);
  if (state.mode === "none") return null;

  const light = appearance === "light";
  const strength = dragging ? 0.95 : 0.62;
  const maxTop = Math.max(0, state.total - state.rows);

  return (
    <div
      ref={trackRef}
      role="scrollbar"
      aria-orientation="vertical"
      aria-label={
        state.mode === "app"
          ? `Scroll ${name} — moves through the agent's own history`
          : `Scroll ${name}`
      }
      aria-valuemin={0}
      aria-valuemax={maxTop}
      aria-valuenow={Math.min(state.top, maxTop)}
      data-testid={`pane-scrollbar-${name}`}
      data-mode={state.mode}
      data-shown={shown ? "true" : "false"}
      onPointerDown={onTrackPointerDown}
      onWheel={onWheel}
      onMouseDown={(event) => event.stopPropagation()}
      className={cn(
        "absolute bottom-1 top-1 z-10 w-[10px] rounded-full transition-opacity duration-150",
        shown ? "opacity-100" : "pointer-events-none opacity-0",
      )}
      style={{
        right: hostGap,
        background: shown
          ? light
            ? "rgba(0,0,0,0.06)"
            : "rgba(255,255,255,0.06)"
          : "transparent",
      }}
    >
      {geometry && (
        <div
          data-testid={`pane-scrollbar-thumb-${name}`}
          onPointerDown={onThumbPointerDown}
          className={cn(
            "absolute left-0 w-full cursor-grab rounded-full",
            dragging ? "cursor-grabbing" : "transition-[top] duration-75",
          )}
          style={{
            top: dragTopPx ?? geometry.topPx,
            height: geometry.heightPx,
            background: `rgb(var(--jarvis-yellow) / ${strength})`,
          }}
        />
      )}
    </div>
  );
}
