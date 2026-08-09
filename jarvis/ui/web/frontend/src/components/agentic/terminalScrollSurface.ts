/**
 * Terminal scrolling with ONE rule: xterm owns scrolling, always.
 *
 * Every pane, every provider, every CLI mode shows the same scrollbar — the
 * exact xterm viewport position — and the wheel always moves xterm's own
 * scrollback. This file used to hold a second "application-owned" regime
 * (owner switching, stroke gestures, PageUp/arrow-key relays, a self-centring
 * grip, an opt-in live-view navigation). Four iterations of it produced eight
 * confirmed defects in one review (2026-08-08, xhigh) — mode flips mid-drag
 * typed keys into the CLI, a 3px stroke step catapulted alternate-screen
 * apps, the rail morphed shapes on a 750ms poll, and a second native
 * scrollbar double-drew next to this one. The maintainer's verdict after
 * living with it was to remove the whole regime.
 *
 * What makes the single rule honest today: the current coding CLIs (Claude
 * Code 2.1.226, Codex) paint the NORMAL buffer line by line — measured via a
 * ConPTY probe, no alt-screen (1049), no mouse tracking at boot — so xterm's
 * scrollback is the real transcript and its position is the real position.
 * A CLI that briefly negotiates mouse tracking mid-session does not get the
 * wheel handed to it: `captureWheelForTerminalHistory` keeps wheel input on
 * xterm's history, which is exactly how Windows Terminal treats these CLIs.
 * Only a true alternate-screen application (vim, less) keeps its negotiated
 * protocols — there xterm has no history, and the rail says so.
 */
import type { Terminal } from "@xterm/xterm";

export interface TerminalScrollView {
  rows: number;
  /** Last xterm viewport line. Zero while no history has scrolled off yet. */
  maxLine: number;
  /** Current xterm viewport line. */
  line: number;
  /** True while an alternate-screen app (vim, less) owns the screen. */
  altScreen: boolean;
}

export interface ScrollThumbGeometry {
  top: number;
  height: number;
}

export const MIN_THUMB_PX = 28;

/** Pixels of wheel travel per scrolled row when the wheel reports pixels. */
const WHEEL_PIXELS_PER_ROW = 40;
/** Ceiling per wheel event, so one coalesced trackpad burst cannot teleport. */
const MAX_ROWS_PER_WHEEL = 40;

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

function finiteWhole(value: unknown): number {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.floor(number)) : 0;
}

/** The exact xterm viewport position — the only scroll truth this UI shows. */
export function readTerminalScrollView(term: Terminal): TerminalScrollView {
  const active = term.buffer?.active;
  const rows = Math.max(1, finiteWhole(term.rows));
  const maxLine = finiteWhole(active?.baseY);
  return {
    rows,
    maxLine,
    line: clamp(finiteWhole(active?.viewportY), 0, maxLine),
    altScreen: (active?.type ?? "normal") === "alternate",
  };
}

/** Thumb geometry. With no history yet the thumb honestly fills the track. */
export function scrollThumbGeometry(
  view: TerminalScrollView,
  trackPx: number,
  minThumbPx = MIN_THUMB_PX,
): ScrollThumbGeometry {
  const track = Math.max(0, trackPx);
  if (track === 0) return { top: 0, height: 0 };
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

/** Map a dragged thumb back to xterm's absolute viewport line. */
export function lineAtThumbTop(
  view: TerminalScrollView,
  topPx: number,
  trackPx: number,
): number {
  if (view.maxLine === 0) return 0;
  const thumb = scrollThumbGeometry(view, trackPx);
  const travel = Math.max(0, trackPx - thumb.height);
  if (travel === 0) return view.maxLine;
  return Math.round(view.maxLine * (clamp(topPx, 0, travel) / travel));
}

/**
 * Keep the wheel on xterm's history even when a normal-buffer CLI has
 * negotiated mouse tracking.
 *
 * Wired via `term.attachCustomWheelEventHandler`. Returning true lets xterm
 * handle the event as usual; returning false means this handler already did.
 *
 * The one intercepted case: normal buffer + mouse tracking on. Left to xterm,
 * that wheel would become mouse reports typed at the CLI — scrolling would
 * "work" only while the CLI feels like it, which is the mode-dependent
 * inconsistency this rebuild removes. An alternate-screen app keeps every
 * negotiated protocol: xterm has no history there to scroll instead.
 * Modifier chords (ctrl=zoom, shift=app escape hatch) stay native.
 */
export function captureWheelForTerminalHistory(
  term: Terminal,
): (event: WheelEvent) => boolean {
  let pixelRemainder = 0;

  return (event: WheelEvent): boolean => {
    if (
      event.deltaY === 0 ||
      Math.abs(event.deltaX) > Math.abs(event.deltaY) ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey
    ) {
      pixelRemainder = 0;
      return true;
    }
    const bufferType = term.buffer?.active?.type ?? "normal";
    const tracking = term.modes?.mouseTrackingMode ?? "none";
    if (bufferType === "alternate" || tracking === "none") {
      pixelRemainder = 0;
      return true;
    }

    const direction = event.deltaY < 0 ? -1 : 1;
    let rows: number;
    if (event.deltaMode === WheelEvent.DOM_DELTA_PAGE) {
      rows = Math.max(1, term.rows) * Math.abs(event.deltaY);
      pixelRemainder = 0;
    } else if (event.deltaMode === WheelEvent.DOM_DELTA_LINE) {
      rows = Math.max(1, Math.ceil(Math.abs(event.deltaY)));
      pixelRemainder = 0;
    } else {
      if (pixelRemainder !== 0 && Math.sign(pixelRemainder) !== direction) {
        pixelRemainder = 0;
      }
      pixelRemainder += event.deltaY;
      rows = Math.floor(Math.abs(pixelRemainder) / WHEEL_PIXELS_PER_ROW);
      if (rows > 0) {
        pixelRemainder -= direction * rows * WHEEL_PIXELS_PER_ROW;
      }
    }
    rows = Math.min(MAX_ROWS_PER_WHEEL, rows);
    if (rows > 0) term.scrollLines(direction * rows);
    // Even a sub-row remainder is accounted; xterm must not ALSO emit a
    // mouse report for the same physical notch.
    return false;
  };
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
