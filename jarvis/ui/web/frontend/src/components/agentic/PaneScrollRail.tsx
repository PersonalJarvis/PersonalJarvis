/**
 * One honest scroll rail for one Agentic-IDE terminal pane.
 *
 * Normal terminal history gets a conventional absolute thumb. Full-screen
 * coding TUIs keep their history inside the application, so their thumb is a
 * centred controller: drag it up or down and the application receives its own
 * standard wheel protocol. It springs back on release because claiming an
 * absolute position there would be a guess.
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
  type TerminalScrollView,
} from "./terminalScrollSurface";

const MOUSE_DRAG_STEP_PX = 7;

interface DragSession {
  pointerId: number;
  owner: TerminalScrollView["owner"];
  grabOffset: number;
  lastY: number;
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

  const moveApplication = useCallback(
    (current: TerminalScrollView, clientY: number) => {
      const drag = dragRef.current;
      const term = terminalRef.current;
      if (!drag || !term) return;
      const delta = clientY - drag.lastY;
      const tracking = term.modes?.mouseTrackingMode ?? "none";
      const step =
        tracking === "none"
          ? Math.max(24, liveTrack().height / 5)
          : MOUSE_DRAG_STEP_PX;
      const notches = Math.floor(Math.abs(delta) / Math.max(1, step));
      if (notches > 0) {
        const direction: -1 | 1 = delta < 0 ? -1 : 1;
        scrollApplication(term, hostRef.current, direction, notches);
        drag.lastY += direction * notches * step;
      }

      // During the gesture the grip follows the hand. It returns to the centre
      // on release, which is the visual contract that this is movement rather
      // than a guessed position.
      const track = liveTrack();
      const thumb = thumbRef.current;
      if (thumb && track.height > 0) {
        const geometry = scrollThumbGeometry(current, track.height);
        const top = Math.min(
          track.height - geometry.height,
          Math.max(0, clientY - track.top - geometry.height / 2),
        );
        thumb.style.top = `${top}px`;
      }
    },
    [hostRef, liveTrack, terminalRef],
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
      if (!grabbedThumb) {
        const direction: -1 | 1 =
          event.clientY - track.top < track.height / 2 ? -1 : 1;
        scrollApplication(
          term,
          hostRef.current,
          direction,
          applicationPageNotches(current.rows),
        );
      }
    },
    [hostRef, liveTrack, moveExact, onFocus, terminalRef],
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
        const geometry = scrollThumbGeometry(current, track.height);
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
        scrollApplication(term, hostRef.current, -1, 1);
      } else if (event.key === "ArrowDown") {
        scrollApplication(term, hostRef.current, 1, 1);
      } else if (event.key === "PageUp") {
        scrollApplication(
          term,
          hostRef.current,
          -1,
          applicationPageNotches(current.rows),
        );
      } else if (event.key === "PageDown") {
        scrollApplication(
          term,
          hostRef.current,
          1,
          applicationPageNotches(current.rows),
        );
      } else {
        handled = false;
      }
      if (!handled) return;
      event.preventDefault();
      event.stopPropagation();
      onFocus?.();
      scheduleSync();
    },
    [hostRef, onFocus, scheduleSync, terminalRef],
  );

  const scrollApplicationPage = useCallback(
    (direction: -1 | 1) => {
      const term = terminalRef.current;
      if (!term) return;
      const current = readTerminalScrollView(term);
      if (current.owner !== "application") return;
      scrollApplication(
        term,
        hostRef.current,
        direction,
        applicationPageNotches(current.rows),
      );
      onFocus?.();
      scheduleSync();
    },
    [hostRef, onFocus, scheduleSync, terminalRef],
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
    : "Application history; drag up or down to scroll";
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
            className="sr-only"
            aria-label={`Scroll older in ${name}`}
            aria-controls={controlsId}
            onClick={() => scrollApplicationPage(-1)}
          />
          <button
            type="button"
            className="sr-only"
            aria-label={`Scroll newer in ${name}`}
            aria-controls={controlsId}
            onClick={() => scrollApplicationPage(1)}
          />
        </>
      )}
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
    </div>
  );
}
