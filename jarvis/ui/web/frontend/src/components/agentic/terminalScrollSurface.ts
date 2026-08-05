/**
 * Provider-neutral terminal scrolling primitives.
 *
 * A terminal has one of two scroll owners:
 *
 * * xterm owns a normal buffer, so its exact line and extent are public;
 * * a full-screen application owns an alternate/mouse-tracking buffer, so the
 *   only truthful operation is to relay scroll input to that application.
 *
 * The second case deliberately has no estimated position. Earlier scrollbars
 * counted wheel events and inferred endpoints from one CLI's screen text; the
 * thumb inevitably drifted or snapped in every other coding CLI. The new rail
 * is an exact scrollbar in the first case and an explicitly centred scroll
 * controller in the second.
 */
import type { Terminal } from "@xterm/xterm";

export type TerminalScrollOwner = "terminal" | "application";

export interface TerminalScrollView {
  owner: TerminalScrollOwner;
  rows: number;
  /** Last exact viewport line. Zero for an application-owned screen. */
  maxLine: number;
  /** Current exact viewport line. Zero for an application-owned screen. */
  line: number;
}

export interface ScrollThumbGeometry {
  top: number;
  height: number;
}

export const MIN_THUMB_PX = 28;
const MAX_RELAY_NOTCHES = 80;
const PAGE_UP = "\x1b[5~";
const PAGE_DOWN = "\x1b[6~";

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

function finiteWhole(value: unknown): number {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.floor(number)) : 0;
}

/** Which side can truthfully answer where this pane is scrolled. */
export function terminalScrollOwner(term: Terminal): TerminalScrollOwner {
  const tracking = term.modes?.mouseTrackingMode ?? "none";
  const bufferType = term.buffer?.active?.type ?? "normal";
  return bufferType === "alternate" || tracking !== "none"
    ? "application"
    : "terminal";
}

/** Read a scroll view without inspecting or interpreting terminal content. */
export function readTerminalScrollView(term: Terminal): TerminalScrollView {
  const owner = terminalScrollOwner(term);
  const rows = Math.max(1, finiteWhole(term.rows));
  if (owner === "application") {
    return { owner, rows, maxLine: 0, line: 0 };
  }
  const active = term.buffer?.active;
  const maxLine = finiteWhole(active?.baseY);
  const line = clamp(finiteWhole(active?.viewportY), 0, maxLine);
  return { owner, rows, maxLine, line };
}

/**
 * Geometry for either an exact thumb or the centred application controller.
 */
export function scrollThumbGeometry(
  view: TerminalScrollView,
  trackPx: number,
  minThumbPx = MIN_THUMB_PX,
): ScrollThumbGeometry {
  const track = Math.max(0, trackPx);
  if (track === 0) return { top: 0, height: 0 };

  if (view.owner === "application") {
    const height = clamp(track * 0.18, Math.min(minThumbPx, track), Math.min(64, track));
    return { top: (track - height) / 2, height };
  }

  if (view.maxLine === 0) return { top: 0, height: track };
  const contentLines = view.maxLine + view.rows;
  const height = clamp(
    track * (view.rows / Math.max(1, contentLines)),
    Math.min(minThumbPx, track),
    track,
  );
  const travel = Math.max(0, track - height);
  return {
    top: travel * (view.line / view.maxLine),
    height,
  };
}

/** Map a dragged exact thumb back to xterm's absolute viewport line. */
export function lineAtThumbTop(
  view: TerminalScrollView,
  topPx: number,
  trackPx: number,
): number {
  if (view.owner !== "terminal" || view.maxLine === 0) return 0;
  const thumb = scrollThumbGeometry(view, trackPx);
  const travel = Math.max(0, trackPx - thumb.height);
  if (travel === 0) return view.maxLine;
  return Math.round(view.maxLine * (clamp(topPx, 0, travel) / travel));
}

/** A page expressed in the wheel reports used by mouse-aware coding TUIs. */
export function applicationPageNotches(rows: number): number {
  // Measured coding TUIs move roughly three transcript rows per report.
  return Math.max(1, Math.ceil(Math.max(1, rows) / 3));
}

/**
 * Relay older/newer movement to the application that owns the screen.
 *
 * Mouse-aware TUIs receive synthetic wheel events through xterm, which already
 * knows the exact protocol the process negotiated. An alternate-screen app
 * without mouse reporting receives the standard PageUp/PageDown key instead.
 */
export function scrollApplication(
  term: Terminal,
  host: HTMLElement | null,
  direction: -1 | 1,
  notches = 1,
): number {
  const count = Math.min(MAX_RELAY_NOTCHES, Math.max(0, Math.floor(notches)));
  if (count === 0) return 0;
  const tracking = term.modes?.mouseTrackingMode ?? "none";
  if (tracking === "none") {
    const pages = Math.max(1, Math.ceil(count / applicationPageNotches(term.rows)));
    for (let index = 0; index < pages; index += 1) {
      term.input(direction < 0 ? PAGE_UP : PAGE_DOWN, true);
    }
    return pages;
  }

  const screen = host?.querySelector<HTMLElement>(".xterm-screen");
  if (!screen || typeof WheelEvent === "undefined") return 0;
  const box = screen.getBoundingClientRect();
  for (let index = 0; index < count; index += 1) {
    screen.dispatchEvent(
      new WheelEvent("wheel", {
        bubbles: true,
        cancelable: true,
        clientX: box.left + box.width / 2,
        clientY: box.top + box.height / 2,
        deltaMode: WheelEvent.DOM_DELTA_LINE,
        deltaY: direction,
      }),
    );
  }
  return count;
}

/**
 * Forward a wheel that landed on the overlay rail to xterm unchanged.
 *
 * The rail observes this passively; the containing terminal region owns the
 * separate job of preventing the workspace behind it from scrolling too.
 */
export function forwardWheelToTerminal(
  host: HTMLElement | null,
  event: WheelEvent,
): boolean {
  const screen = host?.querySelector<HTMLElement>(".xterm-screen");
  if (!screen || typeof WheelEvent === "undefined") return false;
  screen.dispatchEvent(
    new WheelEvent("wheel", {
      bubbles: true,
      cancelable: true,
      clientX: event.clientX,
      clientY: event.clientY,
      deltaMode: event.deltaMode,
      deltaX: event.deltaX,
      deltaY: event.deltaY,
      deltaZ: event.deltaZ,
      ctrlKey: event.ctrlKey,
      shiftKey: event.shiftKey,
      altKey: event.altKey,
      metaKey: event.metaKey,
    }),
  );
  return true;
}

/** Keep terminal wheel input from falling through to the workspace scroller. */
export function bindTerminalScrollRegion(region: HTMLElement): () => void {
  const containWheel = (event: WheelEvent) => {
    event.stopPropagation();
    if (!event.defaultPrevented) event.preventDefault();
  };
  region.addEventListener("wheel", containWheel, { passive: false });
  return () => region.removeEventListener("wheel", containWheel);
}
