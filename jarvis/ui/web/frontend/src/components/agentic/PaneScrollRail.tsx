/**
 * One honest scroll rail for one Agentic-IDE terminal pane.
 *
 * Normal terminal history gets a conventional absolute thumb. Full-screen
 * coding TUIs keep their history inside the application, where no position can
 * be read — so this rail measures one instead: it counts every unit of scroll
 * that reaches the terminal, from its own grip and from the user's wheel alike,
 * and lets the two ends announce themselves by refusing to repaint. See
 * ./terminalScrollSurface for why that is a measurement and not a guess.
 *
 * The grip therefore travels for both owners. It only rests in the middle
 * before the first scroll of an application-owned screen, which is the one
 * moment nothing has been measured yet.
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
  applicationOffsetAtThumbTop,
  applicationPageNotches,
  ApplicationScrollTracker,
  bindTerminalScrollRegion,
  forwardWheelToTerminal,
  lineAtThumbTop,
  readTerminalScrollView,
  screenMoved,
  screenSignature,
  scrollApplication,
  scrollThumbGeometry,
  terminalScrollOwner,
  wheelNotches,
  type ApplicationScrollEstimate,
  type TerminalScrollView,
} from "./terminalScrollSurface";

/**
 * How long a run of relays may stay open before the screen is compared.
 *
 * The application answers over a PTY, so it is never synchronous. Long enough
 * that a busy agent has repainted, short enough that letting go of the wheel at
 * the top of the history feels like an immediate stop.
 */
const SETTLE_MS = 180;

interface DragSession {
  pointerId: number;
  owner: TerminalScrollView["owner"];
  grabOffset: number;
  captured: boolean;
}

interface PendingMeasure {
  direction: -1 | 1;
  notches: number;
  before: string[];
  timer: number;
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

function sameView(
  left: TerminalScrollView | null,
  right: TerminalScrollView,
): boolean {
  return (
    left?.owner === right.owner &&
    left.rows === right.rows &&
    left.maxLine === right.maxLine &&
    left.line === right.line
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
  const trackerRef = useRef<ApplicationScrollTracker>(
    new ApplicationScrollTracker(),
  );
  const measureRef = useRef<PendingMeasure | null>(null);
  const [view, setView] = useState<TerminalScrollView | null>(null);
  const [estimate, setEstimate] = useState<ApplicationScrollEstimate | null>(null);
  const [trackPx, setTrackPx] = useState(0);
  const [dragging, setDragging] = useState(false);

  const forgetMeasurements = useCallback(() => {
    const pending = measureRef.current;
    measureRef.current = null;
    if (pending) window.clearTimeout(pending.timer);
    trackerRef.current.reset();
    setEstimate((current) => (current === null ? current : null));
  }, []);

  const sync = useCallback(() => {
    frameRef.current = null;
    const term = terminalRef.current;
    const host = hostRef.current;
    if (!term) {
      setView(null);
      return;
    }
    const next = readTerminalScrollView(term);
    if (host) host.dataset.scrollOwner = next.owner;
    // Leaving the alternate screen ends the history this rail was counting;
    // whatever it measured belongs to a screen that no longer exists.
    if (next.owner !== "application") forgetMeasurements();
    setView((current) => (sameView(current, next) ? current : next));
  }, [forgetMeasurements, hostRef, terminalRef]);

  const scheduleSync = useCallback(() => {
    if (frameRef.current !== null) return;
    frameRef.current = requestAnimationFrame(sync);
  }, [sync]);

  /**
   * Close the open run of relays.
   *
   * `assumeMoved` covers a reversal: the user turned around before the
   * application had time to answer, so the screen in front of us proves
   * nothing. Only a run the user actually stopped is allowed to declare an end.
   */
  const flushMeasure = useCallback(
    (assumeMoved: boolean) => {
      const pending = measureRef.current;
      measureRef.current = null;
      if (!pending) return;
      window.clearTimeout(pending.timer);
      const term = terminalRef.current;
      if (!term) return;
      const moved =
        assumeMoved || screenMoved(pending.before, screenSignature(term));
      trackerRef.current.settle(pending.direction, pending.notches, moved);
      setEstimate(trackerRef.current.estimate());
    },
    [terminalRef],
  );

  /**
   * Count scroll that has just been handed to an application-owned screen.
   *
   * Every route ends up here — this rail's grip, its arrow caps, its keys and
   * the wheel the user rolls over the terminal itself — because a position
   * measured from only some of them would be wrong the first time the user
   * touched the other.
   */
  const noteRelay = useCallback(
    (direction: -1 | 1, notches: number) => {
      const term = terminalRef.current;
      if (!term || notches <= 0) return;
      if (terminalScrollOwner(term) !== "application") return;
      trackerRef.current.advance(direction, notches);
      setEstimate(trackerRef.current.estimate());

      const pending = measureRef.current;
      if (pending && pending.direction === direction) {
        pending.notches += notches;
        window.clearTimeout(pending.timer);
        pending.timer = window.setTimeout(() => flushMeasure(false), SETTLE_MS);
        return;
      }
      if (pending) flushMeasure(true);
      measureRef.current = {
        direction,
        notches,
        // Taken now, while the report is still travelling down the PTY: the
        // screen cannot have answered inside this handler.
        before: screenSignature(term),
        timer: window.setTimeout(() => flushMeasure(false), SETTLE_MS),
      };
    },
    [flushMeasure, terminalRef],
  );

  /** Relay to the application and count it, whichever protocol it speaks. */
  const relayApplication = useCallback(
    (direction: -1 | 1, notches: number) => {
      const term = terminalRef.current;
      if (!term) return;
      const sent = scrollApplication(term, hostRef.current, direction, notches);
      // Mouse-mode relays are wheel events on the terminal, so the listener
      // below already counted them. The page-key fallback emits nothing a
      // listener could see, so it reports itself here, converted to the same
      // unit the wheel path counts in.
      if ((term.modes?.mouseTrackingMode ?? "none") === "none" && sent > 0) {
        noteRelay(direction, sent * applicationPageNotches(term.rows));
      }
    },
    [hostRef, noteRelay, terminalRef],
  );

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const count = (event: WheelEvent) => {
      const term = terminalRef.current;
      if (!term) return;
      const notches = wheelNotches(term, host, event);
      if (notches > 0) noteRelay(event.deltaY < 0 ? -1 : 1, notches);
    };
    // Passive and on the host: every wheel that reaches this terminal — real or
    // synthesised — is dispatched on the screen inside it and bubbles here.
    host.addEventListener("wheel", count, { passive: true });
    return () => host.removeEventListener("wheel", count);
  }, [hostRef, noteRelay, terminalRef]);

  useEffect(() => forgetMeasurements, [epoch, forgetMeasurements]);

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
    // A replayed session can hand this rail a terminal whose mouse-tracking
    // DECSET was parsed before the subscriptions above existed, and a TUI can
    // flip tracking without emitting anything else afterwards. Observed live:
    // panes stuck on "no history" while the application was already scrolling.
    // A slow poll is the safety net; sameView keeps it render-free.
    const timer = window.setInterval(scheduleSync, 750);
    return () => window.clearInterval(timer);
  }, [scheduleSync]);

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

  const moveExact = useCallback(
    (current: TerminalScrollView, clientY: number, grabOffset: number) => {
      const term = terminalRef.current;
      if (!term) return;
      const track = liveTrack();
      if (track.height <= 0) return;
      const thumbTop = clientY - track.top - grabOffset;
      term.scrollToLine(lineAtThumbTop(current, thumbTop, track.height));
      scheduleSync();
    },
    [liveTrack, scheduleSync, terminalRef],
  );

  /**
   * Drag an application grip to an absolute offset.
   *
   * The gesture states where the user wants to be and this sends the exact
   * difference from where the count says they are — the same contract as the
   * exact thumb, over a measured scale instead of a read one.
   */
  const moveApplication = useCallback(
    (current: TerminalScrollView, clientY: number) => {
      const drag = dragRef.current;
      const term = terminalRef.current;
      if (!drag || !term) return;
      const track = liveTrack();
      if (track.height <= 0) return;
      const measured = trackerRef.current.estimate();
      const geometry = scrollThumbGeometry(current, track.height, {
        estimate: measured,
      });
      const top = Math.min(
        Math.max(0, track.height - geometry.height),
        Math.max(0, clientY - track.top - drag.grabOffset),
      );
      const delta =
        applicationOffsetAtThumbTop(measured, top, track.height) -
        measured.offset;
      if (delta !== 0) relayApplication(delta > 0 ? -1 : 1, Math.abs(delta));

      // The grip follows the hand for the length of the gesture; on release the
      // measured position takes the thumb back, which is where the count and
      // the hand agree unless the application ran out of history on the way.
      const thumb = thumbRef.current;
      if (thumb) thumb.style.top = `${top}px`;
    },
    [liveTrack, relayApplication, terminalRef],
  );

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      const term = terminalRef.current;
      if (!term) return;
      const current = readTerminalScrollView(term);
      const track = liveTrack();
      if (track.height <= 0) return;
      event.preventDefault();
      event.stopPropagation();
      onFocus?.();

      const geometry = scrollThumbGeometry(current, track.height, {
        estimate: trackerRef.current.estimate(),
      });
      const grabbedThumb = event.target === thumbRef.current;
      const grabOffset = grabbedThumb
        ? event.clientY - track.top - geometry.top
        : geometry.height / 2;
      dragRef.current = {
        pointerId: event.pointerId,
        owner: current.owner,
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

      if (current.owner === "terminal") {
        if (!grabbedThumb) moveExact(current, event.clientY, grabOffset);
        return;
      }
      // A click beside the grip means the same thing it means on any scrollbar
      // now that there is a scale to aim at: go there.
      if (!grabbedThumb) moveApplication(current, event.clientY);
    },
    [liveTrack, moveApplication, moveExact, onFocus, terminalRef],
  );

  const onPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      const term = terminalRef.current;
      if (!drag || !term || drag.pointerId !== event.pointerId) return;
      event.preventDefault();
      const current = readTerminalScrollView(term);
      if (drag.owner === "terminal" && current.owner === "terminal") {
        moveExact(current, event.clientY, drag.grabOffset);
      } else {
        moveApplication(current, event.clientY);
      }
    },
    [moveApplication, moveExact, terminalRef],
  );

  const finishDrag = useCallback(
    (pointerId?: number) => {
      const drag = dragRef.current;
      if (!drag || (pointerId !== undefined && pointerId !== drag.pointerId)) return;
      dragRef.current = null;
      setDragging(false);
      // Restore from the current owner, not the owner at pointer-down. A TUI
      // may enter or leave its alternate buffer while the gesture is active.
      const term = terminalRef.current;
      const thumb = thumbRef.current;
      const track = liveTrack();
      if (term && thumb && track.height > 0) {
        const current = readTerminalScrollView(term);
        const geometry = scrollThumbGeometry(current, track.height, {
          estimate: trackerRef.current.estimate(),
        });
        thumb.style.top = `${geometry.top}px`;
      }
    },
    [liveTrack, terminalRef],
  );

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
      if (current.owner === "terminal") {
        if (event.key === "ArrowUp") term.scrollLines(-1);
        else if (event.key === "ArrowDown") term.scrollLines(1);
        else if (event.key === "PageUp") term.scrollLines(-current.rows);
        else if (event.key === "PageDown") term.scrollLines(current.rows);
        else if (event.key === "Home") term.scrollToTop();
        else if (event.key === "End") term.scrollToBottom();
        else handled = false;
      } else if (event.key === "ArrowUp") {
        relayApplication(-1, 1);
      } else if (event.key === "ArrowDown") {
        relayApplication(1, 1);
      } else if (event.key === "PageUp") {
        relayApplication(-1, applicationPageNotches(current.rows));
      } else if (event.key === "PageDown") {
        relayApplication(1, applicationPageNotches(current.rows));
      } else {
        handled = false;
      }
      if (!handled) return;
      event.preventDefault();
      event.stopPropagation();
      onFocus?.();
      scheduleSync();
    },
    [onFocus, relayApplication, scheduleSync, terminalRef],
  );

  const scrollApplicationPage = useCallback(
    (direction: -1 | 1) => {
      const term = terminalRef.current;
      if (!term) return;
      const current = readTerminalScrollView(term);
      if (current.owner !== "application") return;
      relayApplication(direction, applicationPageNotches(current.rows));
      onFocus?.();
      scheduleSync();
    },
    [onFocus, relayApplication, scheduleSync, terminalRef],
  );

  const geometry = useMemo(
    () =>
      view ? scrollThumbGeometry(view, trackPx, { estimate }) : { top: 0, height: 0 },
    [estimate, trackPx, view],
  );
  if (!view) return null;

  const exact = view.owner === "terminal";
  const applicationText = !estimate
    ? "Application history; drag up or down to scroll"
    : estimate.atBottom
      ? "Application history, at the newest end"
      : estimate.atTop
        ? "Application history, at the oldest end"
        : estimate.calibrated
          ? `Application history, ${Math.round((1 - estimate.offset / estimate.span) * 100)}% from the oldest end`
          : "Application history, position measured so far";
  const valueText = exact
    ? view.maxLine === 0
      ? "No terminal history yet"
      : `Terminal line ${view.line} of ${view.maxLine}`
    : applicationText;
  const shell = PANE_CHROME[appearance].shell;

  return (
    <div
      ref={trackRef}
      role={exact ? "scrollbar" : "group"}
      aria-label={exact ? `Scroll ${name}` : `Scroll ${name} application history`}
      aria-controls={controlsId}
      aria-orientation={exact ? "vertical" : undefined}
      aria-valuemin={exact ? 0 : undefined}
      aria-valuemax={exact ? Math.max(1, view.maxLine) : undefined}
      aria-valuenow={exact ? view.line : undefined}
      aria-valuetext={exact ? valueText : undefined}
      tabIndex={exact ? 0 : undefined}
      data-testid={`pane-scroll-rail-${name}`}
      data-scroll-mode={view.owner}
      data-scroll-position={
        exact
          ? "exact"
          : !estimate
            ? "unmeasured"
            : estimate.calibrated
              ? "calibrated"
              : "measuring"
      }
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
        "opacity-65 transition-opacity hover:opacity-100 focus-visible:opacity-100 focus-within:opacity-100",
        "focus-visible:ring-1 focus-visible:ring-[#e7c46e]/80 focus-within:ring-1 focus-within:ring-[#e7c46e]/80",
        dragging ? "cursor-grabbing opacity-100" : "cursor-grab",
      )}
      style={{ background: shell }}
    >
      {!exact && (
        <>
          <button
            type="button"
            aria-label={`Scroll older in ${name}`}
            aria-controls={controlsId}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => scrollApplicationPage(-1)}
            className={cn(
              "absolute inset-x-0 top-0 flex h-4 items-start justify-center rounded-t-full pt-1",
              "text-[#e7c46e]/70 outline-none hover:text-[#e7c46e] focus-visible:text-[#e7c46e]",
            )}
          >
            <svg viewBox="0 0 8 5" className="h-[5px] w-2 fill-current" aria-hidden="true">
              <path d="M4 0 8 5H0Z" />
            </svg>
          </button>
          <button
            type="button"
            aria-label={`Scroll newer in ${name}`}
            aria-controls={controlsId}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => scrollApplicationPage(1)}
            className={cn(
              "absolute inset-x-0 bottom-0 flex h-4 items-end justify-center rounded-b-full pb-1",
              "text-[#e7c46e]/70 outline-none hover:text-[#e7c46e] focus-visible:text-[#e7c46e]",
            )}
          >
            <svg viewBox="0 0 8 5" className="h-[5px] w-2 fill-current" aria-hidden="true">
              <path d="M4 5 0 0h8Z" />
            </svg>
          </button>
        </>
      )}
      <div
        ref={thumbRef}
        data-pane-scroll-thumb="true"
        data-testid={`pane-scroll-thumb-${name}`}
        className={cn(
          "absolute rounded-full bg-[#e7c46e]/65",
          exact ? "right-[3px] w-1.5" : "right-[2px] w-2",
          "shadow-[0_0_0_1px_rgba(0,0,0,0.18)] transition-[background-color,top]",
          "group-hover:bg-[#e7c46e]/90 group-focus-visible:bg-[#e7c46e]/90",
          dragging && "bg-[#e7c46e] transition-none",
        )}
        style={{ top: geometry.top, height: geometry.height }}
      >
        {!exact && (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 top-1/2 flex -translate-y-1/2 flex-col items-center gap-[3px]"
          >
            <span className="h-[2px] w-[2px] rounded-full bg-[#141414]/70" />
            <span className="h-[2px] w-[2px] rounded-full bg-[#141414]/70" />
            <span className="h-[2px] w-[2px] rounded-full bg-[#141414]/70" />
          </span>
        )}
      </div>
    </div>
  );
}
