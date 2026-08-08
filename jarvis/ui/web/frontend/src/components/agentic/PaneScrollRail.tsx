/**
 * One honest scroll rail for one Agentic-IDE terminal pane.
 *
 * Normal terminal history gets a conventional absolute thumb: xterm knows the
 * line and the extent, so the thumb is the truth and dragging it is a seek.
 *
 * A full-screen coding TUI keeps its history inside the application, and this
 * rail then draws NO POSITION AT ALL. It becomes what it can honestly be —
 * arrow caps that page, a strip that scrolls while stroked, the wheel passing
 * through, and a GRIP that makes the stroke visible and grabbable: it rests in
 * the middle, follows the hand while dragging, and springs back to the middle
 * on release. The spring-back is the honesty — a handle that always returns to
 * centre cannot be read as "you are here".
 *
 * Two attempts to show an actual position there were built and reverted after
 * the maintainer used them; ./terminalScrollSurface records both and why. The
 * short version: any shape that STAYS where the drag left it gets read as
 * "you are here", and being slightly wrong about that is worse than saying
 * nothing.
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
  applicationPageNotches,
  bindTerminalScrollRegion,
  forwardWheelToTerminal,
  lineAtThumbTop,
  readTerminalScrollView,
  scrollApplication,
  scrollThumbGeometry,
  strokeUnits,
  type TerminalScrollView,
} from "./terminalScrollSurface";

interface DragSession {
  pointerId: number;
  owner: TerminalScrollView["owner"];
  grabOffset: number;
  /** Where the stroke was last accounted for. Application rails only. */
  lastY: number;
  /** Where the pointer went down — the grip's visual travel is measured from
   * here so it can spring back to centre on release. Application rails only. */
  originY: number;
  captured: boolean;
}

/** Height of the application grip capsule, in px. Must match the render. */
const GRIP_PX = 28;
/** Height of one arrow cap, which the grip must never slide under. */
const CAP_PX = 16;

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
  const [view, setView] = useState<TerminalScrollView | null>(null);
  const [trackPx, setTrackPx] = useState(0);
  const [dragging, setDragging] = useState(false);
  /** The application grip's travel from centre while a stroke is held. */
  const [gripOffset, setGripOffset] = useState(0);

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
    setView((current) => (sameView(current, next) ? current : next));
  }, [hostRef, terminalRef]);

  const scheduleSync = useCallback(() => {
    if (frameRef.current !== null) return;
    frameRef.current = requestAnimationFrame(sync);
  }, [sync]);

  /** Relay to the application, whichever protocol it speaks. */
  const relayApplication = useCallback(
    (direction: -1 | 1, notches: number) => {
      const term = terminalRef.current;
      if (!term) return;
      scrollApplication(term, hostRef.current, direction, notches);
    },
    [hostRef, terminalRef],
  );

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
   * Stroke an application rail: distance moved becomes distance scrolled.
   *
   * Not a seek — there is no scale to seek on. The pointer's travel since the
   * last accounted position is converted to relay units and the remainder is
   * carried, so a slow drag scrolls smoothly and a fast one scrolls far.
   */
  const strokeApplication = useCallback(
    (clientY: number) => {
      const drag = dragRef.current;
      if (!drag) return;
      const { units, consumedPx } = strokeUnits(clientY - drag.lastY);
      if (units === 0) return;
      relayApplication(consumedPx < 0 ? -1 : 1, units);
      drag.lastY += consumedPx;
    },
    [relayApplication],
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

      const geometry = scrollThumbGeometry(current, track.height);
      const grabbedThumb = event.target === thumbRef.current;
      const grabOffset = grabbedThumb
        ? event.clientY - track.top - geometry.top
        : geometry.height / 2;
      dragRef.current = {
        pointerId: event.pointerId,
        owner: current.owner,
        grabOffset,
        lastY: event.clientY,
        originY: event.clientY,
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
      // A press on an application rail starts a stroke and moves nothing yet:
      // pressing must not teleport the transcript to a place the press did not
      // mean, and there is no position for it to have meant.
    },
    [liveTrack, moveExact, onFocus, terminalRef],
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
        strokeApplication(event.clientY);
        // The grip follows the hand, clamped so it never slides under an
        // arrow cap. finishDrag springs it back to centre.
        const travel = Math.max(0, liveTrack().height / 2 - CAP_PX - GRIP_PX / 2);
        setGripOffset(
          Math.max(-travel, Math.min(travel, event.clientY - drag.originY)),
        );
      }
    },
    [liveTrack, moveExact, strokeApplication, terminalRef],
  );

  const finishDrag = useCallback(
    (pointerId?: number) => {
      const drag = dragRef.current;
      if (!drag || (pointerId !== undefined && pointerId !== drag.pointerId)) return;
      dragRef.current = null;
      setDragging(false);
      // The grip is a control, not an indicator: it always returns to centre,
      // which is what keeps it from ever being read as a position.
      setGripOffset(0);
      // Restore from the current owner, not the owner at pointer-down. A TUI
      // may enter or leave its alternate buffer while the gesture is active,
      // and an exact thumb left where the hand dropped it would be a lie about
      // a screen that can answer for itself.
      const term = terminalRef.current;
      const thumb = thumbRef.current;
      const track = liveTrack();
      if (term && thumb && track.height > 0) {
        const current = readTerminalScrollView(term);
        thumb.style.top = `${scrollThumbGeometry(current, track.height).top}px`;
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
    () => (view ? scrollThumbGeometry(view, trackPx) : { top: 0, height: 0 }),
    [trackPx, view],
  );
  if (!view) return null;

  const exact = view.owner === "terminal";
  const valueText = exact
    ? view.maxLine === 0
      ? "No terminal history yet"
      : `Terminal line ${view.line} of ${view.maxLine}`
    : `Scroll ${name}: this app keeps its own history, so there is no position to show`;
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
      data-scroll-position={exact ? "exact" : "none"}
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
        "transition-opacity hover:opacity-100 focus-visible:opacity-100 focus-within:opacity-100",
        // BOTH rail kinds stay legible at rest, at the same level. The
        // application rail used to hide until the pointer entered its pane,
        // and a control that is invisible next to a sibling pane's visible
        // thumb reads as "the scrollbar is broken in this CLI".
        "opacity-65",
        "focus-visible:ring-1 focus-visible:ring-[#e7c46e]/80 focus-within:ring-1 focus-within:ring-[#e7c46e]/80",
        // `!` so a stroke that carries the pointer out of the pane does not
        // fade the rail out from under the hand still holding it.
        dragging ? "cursor-grabbing !opacity-100" : "cursor-grab",
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
      {/*
        The application grip: grabbable, but positionless. It rests in the
        middle, rides along while a stroke is held, and springs back on
        release — see the module docstring for why it must never stay where
        the drag left it. Pointer events bubble to the track's stroke
        handlers; the grip itself adds nothing but a place to hold.
      */}
      {!exact && (
        <div
          data-testid={`pane-scroll-grip-${name}`}
          aria-hidden="true"
          className={cn(
            "absolute left-1/2 top-1/2 w-2 rounded-full bg-[#e7c46e]/45",
            "shadow-[0_0_0_1px_rgba(0,0,0,0.18)]",
            "group-hover:bg-[#e7c46e]/80 group-focus-within:bg-[#e7c46e]/80",
            dragging
              ? "bg-[#e7c46e] transition-none"
              : "transition-transform duration-200",
          )}
          style={{
            height: GRIP_PX,
            transform: `translate(-50%, calc(-50% + ${gripOffset}px))`,
          }}
        >
          <svg
            viewBox="0 0 8 28"
            className="h-full w-full fill-[#141210]/70"
            aria-hidden="true"
          >
            <circle cx="4" cy="10" r="1.1" />
            <circle cx="4" cy="14" r="1.1" />
            <circle cx="4" cy="18" r="1.1" />
          </svg>
        </div>
      )}
      {/*
        The thumb exists only where a position does — an application pane gets
        the self-centring grip above instead, never a shape that stays where
        the drag left it.
      */}
      {exact && (
        <div
          ref={thumbRef}
          data-pane-scroll-thumb="true"
          data-testid={`pane-scroll-thumb-${name}`}
          className={cn(
            "absolute right-[3px] w-1.5 rounded-full bg-[#e7c46e]/65",
            "shadow-[0_0_0_1px_rgba(0,0,0,0.18)] transition-[background-color,top]",
            "group-hover:bg-[#e7c46e]/90 group-focus-visible:bg-[#e7c46e]/90",
            dragging && "bg-[#e7c46e] transition-none",
          )}
          style={{ top: geometry.top, height: geometry.height }}
        />
      )}
    </div>
  );
}
