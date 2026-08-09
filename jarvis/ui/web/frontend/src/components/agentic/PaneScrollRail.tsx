/**
 * One scroll rail for one Agentic-IDE terminal pane — the SAME rail for every
 * provider and every CLI mode.
 *
 * It draws xterm's exact viewport position as a conventional thumb: dragging
 * seeks, clicking the track jumps, arrow/page keys scroll, and the wheel
 * passes through to the terminal. There is no second regime. The previous
 * owner-switching rail (grip, stroke gestures, key relays, live-view opt-in)
 * accumulated eight confirmed defects across four iterations and was removed
 * whole on the maintainer's verdict — see ./terminalScrollSurface for why the
 * single xterm-owned rule is honest for today's coding CLIs.
 *
 * While an alternate-screen app (vim, less) holds the screen, xterm has no
 * history: the thumb fills the track and the rail says so in its tooltip.
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from "react";
import type { Terminal } from "@xterm/xterm";
import { cn } from "@/lib/utils";
import { PANE_CHROME, type TerminalAppearance } from "./terminalThemes";
import {
  bindTerminalScrollRegion,
  forwardWheelToTerminal,
  lineAtThumbTop,
  readTerminalScrollView,
  scrollThumbGeometry,
  type TerminalScrollView,
} from "./terminalScrollSurface";

interface DragSession {
  pointerId: number;
  /** Pixel offset between the grab point and the thumb's top edge. */
  grabOffset: number;
  captured: boolean;
}

interface PaneScrollRailProps {
  name: string;
  controlsId: string;
  regionRef: RefObject<HTMLElement | null>;
  hostRef: RefObject<HTMLElement | null>;
  terminalRef: RefObject<Terminal | null>;
  /** Bumped whenever the ref above receives a new xterm instance. */
  epoch: number;
  appearance: TerminalAppearance;
  onFocus?: () => void;
}

/**
 * How often the rail re-reads the terminal without being told to.
 *
 * xterm's events cover output and scrolling, but a pane that has STOPPED
 * emitting output still has a position to state, and the events that would
 * have announced it can land before this rail subscribes — a replayed session
 * writes its whole scrollback in one burst at mount. Without this the rail
 * kept whatever it read first, which on an idle pane was "no history yet", and
 * a full-track thumb on a pane full of history reads as a broken scrollbar
 * (reported 2026-08-09). `sameView` keeps a tick that changes nothing free.
 */
const RESYNC_MS = 500;

function sameView(
  left: TerminalScrollView | null,
  right: TerminalScrollView,
): boolean {
  return (
    left?.rows === right.rows &&
    left.maxLine === right.maxLine &&
    left.line === right.line &&
    left.altScreen === right.altScreen &&
    left.hiddenHistory === right.hiddenHistory
  );
}

export function PaneScrollRail({
  name,
  controlsId,
  regionRef,
  hostRef,
  terminalRef,
  epoch,
  appearance,
  onFocus,
}: PaneScrollRailProps) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const thumbRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragSession | null>(null);
  const frameRef = useRef<number | null>(null);
  const [view, setView] = useState<TerminalScrollView | null>(null);
  const [trackPx, setTrackPx] = useState(0);
  const [dragging, setDragging] = useState(false);

  const sync = useCallback(() => {
    frameRef.current = null;
    const term = terminalRef.current;
    if (!term) {
      setView(null);
      return;
    }
    const next = readTerminalScrollView(term);
    setView((current) => (sameView(current, next) ? current : next));
  }, [terminalRef]);

  const scheduleSync = useCallback(() => {
    if (frameRef.current !== null) return;
    frameRef.current = requestAnimationFrame(sync);
  }, [sync]);

  useEffect(() => {
    const term = terminalRef.current;
    if (!term) {
      setView(null);
      return;
    }
    sync();
    const subscriptions = [
      term.onScroll?.(scheduleSync),
      term.onWriteParsed?.(scheduleSync),
      term.buffer?.onBufferChange?.(scheduleSync),
    ];
    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      for (const subscription of subscriptions) subscription?.dispose();
    };
  }, [epoch, scheduleSync, sync, terminalRef]);

  useEffect(() => {
    // Deliberately `sync`, not `scheduleSync`: the whole point of this timer is
    // to be the path that cannot be starved, and the rAF gate is exactly what
    // an unpainted or throttled pane withholds.
    const timer = window.setInterval(sync, RESYNC_MS);
    return () => window.clearInterval(timer);
  }, [sync]);

  useEffect(() => {
    const region = regionRef.current;
    if (!region) return;
    return bindTerminalScrollRegion(region);
  }, [regionRef]);

  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;
    const forward = (event: WheelEvent) => {
      forwardWheelToTerminal(hostRef.current, event);
    };
    // Passive by design: this listener only clones the input. The terminal
    // region contains the browser default after xterm has received the clone.
    track.addEventListener("wheel", forward, { passive: true });
    return () => track.removeEventListener("wheel", forward);
  }, [hostRef, view !== null]);

  useLayoutEffect(() => {
    const track = trackRef.current;
    if (!track) return;
    const measure = () => {
      const height = track.clientHeight || track.getBoundingClientRect().height;
      setTrackPx((current) => (current === height ? current : height));
    };
    measure();
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(track);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [view !== null]);

  const liveTrack = useCallback(() => {
    const track = trackRef.current;
    if (!track) return { top: 0, height: 0 };
    const box = track.getBoundingClientRect();
    return { top: box.top, height: track.clientHeight || box.height };
  }, []);

  const moveTo = useCallback(
    (clientY: number, grabOffset: number) => {
      const term = terminalRef.current;
      if (!term) return;
      const current = readTerminalScrollView(term);
      const track = liveTrack();
      if (track.height <= 0) return;
      const thumbTop = clientY - track.top - grabOffset;
      term.scrollToLine(lineAtThumbTop(current, thumbTop, track.height));
      scheduleSync();
    },
    [liveTrack, scheduleSync, terminalRef],
  );

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      const term = terminalRef.current;
      if (!term) return;
      const track = liveTrack();
      if (track.height <= 0) return;
      event.preventDefault();
      event.stopPropagation();
      onFocus?.();

      const current = readTerminalScrollView(term);
      const geometry = scrollThumbGeometry(current, track.height);
      const grabbedThumb = event.target === thumbRef.current;
      const grabOffset = grabbedThumb
        ? event.clientY - track.top - geometry.top
        : geometry.height / 2;
      dragRef.current = {
        pointerId: event.pointerId,
        grabOffset,
        captured: false,
      };
      setDragging(true);
      try {
        event.currentTarget.setPointerCapture(event.pointerId);
        dragRef.current.captured =
          event.currentTarget.hasPointerCapture?.(event.pointerId) ?? true;
      } catch {
        // Older WebViews and jsdom have no pointer-capture implementation.
      }
      // A press on the bare track is a jump to that spot; the drag continues
      // from there. A press on the thumb keeps the line under the hand.
      if (!grabbedThumb) moveTo(event.clientY, grabOffset);
    },
    [liveTrack, moveTo, onFocus, terminalRef],
  );

  const onPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      event.preventDefault();
      moveTo(event.clientY, drag.grabOffset);
    },
    [moveTo],
  );

  const finishDrag = useCallback((pointerId?: number) => {
    const drag = dragRef.current;
    if (!drag || (pointerId !== undefined && pointerId !== drag.pointerId)) {
      return;
    }
    dragRef.current = null;
    setDragging(false);
  }, []);

  const onPointerUp = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      finishDrag(event.pointerId);
      try {
        event.currentTarget.releasePointerCapture(event.pointerId);
      } catch {
        // No capture was available; the drag still completed on this event.
      }
    },
    [finishDrag],
  );

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      const term = terminalRef.current;
      if (!term) return;
      const current = readTerminalScrollView(term);
      let handled = true;
      if (event.key === "ArrowUp") term.scrollLines(-1);
      else if (event.key === "ArrowDown") term.scrollLines(1);
      else if (event.key === "PageUp") term.scrollLines(-current.rows);
      else if (event.key === "PageDown") term.scrollLines(current.rows);
      else if (event.key === "Home") term.scrollToTop();
      else if (event.key === "End") term.scrollToBottom();
      else handled = false;
      if (!handled) return;
      event.preventDefault();
      event.stopPropagation();
      onFocus?.();
      scheduleSync();
    },
    [onFocus, scheduleSync, terminalRef],
  );

  const geometry = useMemo(
    () => (view ? scrollThumbGeometry(view, trackPx) : { top: 0, height: 0 }),
    [trackPx, view],
  );
  if (!view) return null;

  // Three honest states, and the rail LOOKS different in each — a full-track
  // thumb that means "nothing to scroll" is indistinguishable from a stuck one
  // unless it also stops looking like a grip.
  const state = view.altScreen ? "app" : view.maxLine === 0 ? "empty" : "history";
  const valueText =
    state === "app"
      ? view.hiddenHistory > 0
        ? `Scroll ${name}: a full-screen app owns this pane — its earlier history is in the pane history above`
        : `Scroll ${name}: a full-screen app owns this pane right now`
      : state === "empty"
        ? `Scroll ${name}: nothing has scrolled out of view yet`
        : `Terminal line ${view.line} of ${view.maxLine}`;
  const shell = PANE_CHROME[appearance].shell;

  return (
    <div
      ref={trackRef}
      role="scrollbar"
      aria-label={`Scroll ${name}`}
      aria-controls={controlsId}
      aria-orientation="vertical"
      aria-valuemin={0}
      aria-valuemax={Math.max(1, view.maxLine)}
      aria-valuenow={view.line}
      aria-valuetext={valueText}
      tabIndex={0}
      data-testid={`pane-scroll-rail-${name}`}
      data-scroll-state={state}
      title={valueText}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={(event) => finishDrag(event.pointerId)}
      onLostPointerCapture={(event) => finishDrag(event.pointerId)}
      onPointerLeave={() => {
        if (dragRef.current && !dragRef.current.captured) finishDrag();
      }}
      onKeyDown={onKeyDown}
      className={cn(
        "group absolute inset-y-1 right-0 z-10 w-3 touch-none select-none rounded-full outline-none",
        "opacity-65 transition-opacity hover:opacity-100 focus-visible:opacity-100",
        "focus-visible:ring-1 focus-visible:ring-[#e7c46e]/80",
        // `!` so a drag that carries the pointer out of the pane does not
        // fade the rail out from under the hand still holding it.
        dragging ? "cursor-grabbing !opacity-100" : "cursor-grab",
      )}
      style={{ background: shell }}
    >
      <div
        ref={thumbRef}
        data-pane-scroll-thumb="true"
        data-testid={`pane-scroll-thumb-${name}`}
        className={cn(
          "absolute right-[3px] w-1.5 rounded-full",
          "shadow-[0_0_0_1px_rgba(0,0,0,0.18)] transition-[background-color,top]",
          state === "history"
            ? "bg-[#e7c46e]/65 group-hover:bg-[#e7c46e]/90 group-focus-visible:bg-[#e7c46e]/90"
            : // Nothing to scroll: a faint full-length track, never a bright
              // bar that reads as a grip stuck at full height.
              "bg-[#e7c46e]/20",
          dragging && state === "history" && "bg-[#e7c46e] transition-none",
        )}
        style={{ top: geometry.top, height: geometry.height }}
      />
    </div>
  );
}
