/**
 * Provider-neutral terminal scrolling primitives.
 *
 * A terminal has one of two scroll owners:
 *
 * * xterm owns a normal buffer, so its exact line and extent are public;
 * * a full-screen application owns an alternate/mouse-tracking buffer, so the
 *   only truthful operation is to relay scroll input to that application.
 *
 * In the second case there is NO POSITION AND NO THUMB. Not a centred one, not
 * an estimated one — none, so that nothing on screen can be read as an answer to
 * "where am I". The rail there is a control: arrow caps that page, a strip that
 * scrolls when stroked, and the wheel passing through.
 *
 * Two attempts to show a position anyway are on record, both reverted after the
 * maintainer used them:
 *
 * 1. Counting wheel events and reading one CLI's screen text for its endpoints.
 *    Drifted in that CLI and was wrong in every other one.
 * 2. Counting relayed units and treating "the application did not repaint" as an
 *    end (2026-08-08). Sound on paper and green in tests; in the real pane the
 *    grip still read as stuck near the middle, a drag to the top moved the
 *    transcript by a fraction of the way, and letting go put the grip back.
 *
 * The lesson both times is the same: a full-screen application's scroll state
 * lives in that process, an approximation of it LOOKS exactly like a position,
 * and a position that is even slightly wrong is worse than an honest control —
 * the user trusts it, acts on it, and is misled. Do not try a third time unless
 * the CLI itself reports where it is.
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

/**
 * Pixels of pointer travel per relayed unit when stroking an application rail.
 *
 * A stroke, not a jump to a position: the gesture says "keep going this way",
 * which is the only thing the rail can honestly promise when the application
 * owns the history. Roughly half a screen per rail-length drag.
 */
export const STROKE_STEP_PX = 7;

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
 * Geometry for the exact thumb, and the absence of one everywhere else.
 *
 * A zero height means "draw no thumb" — the caller renders nothing rather than
 * a shape the user would read as a position. See the module docstring.
 */
export function scrollThumbGeometry(
  view: TerminalScrollView,
  trackPx: number,
  minThumbPx = MIN_THUMB_PX,
): ScrollThumbGeometry {
  const track = Math.max(0, trackPx);
  if (track === 0) return { top: 0, height: 0 };
  if (view.owner === "application") return { top: 0, height: 0 };

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

/**
 * Turn pointer travel along the rail into relayed units.
 *
 * Returns whole units and the pixels they consumed, so the caller can carry the
 * remainder into the next pointer event instead of losing it — a stroke has to
 * feel continuous even though the relay protocol is discrete.
 */
export function strokeUnits(
  deltaPx: number,
  stepPx = STROKE_STEP_PX,
): { units: number; consumedPx: number } {
  const step = Math.max(1, stepPx);
  const units = Math.floor(Math.abs(deltaPx) / step);
  if (units === 0) return { units: 0, consumedPx: 0 };
  return { units, consumedPx: Math.sign(deltaPx) * units * step };
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
