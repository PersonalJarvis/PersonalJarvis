/**
 * Provider-neutral terminal scrolling primitives.
 *
 * A terminal has one of two scroll owners:
 *
 * * xterm owns a normal buffer, so its exact line and extent are public;
 * * a full-screen application owns an alternate/mouse-tracking buffer, or the
 *   user explicitly activates a keyboard-owned inline view, so the only
 *   truthful operation is to relay scroll input to that application.
 *
 * In the second case there is NO POSITION. Not an estimated one — none, so
 * that nothing on screen can be read as an answer to "where am I". The rail
 * there is a control: arrow caps that page, a strip that scrolls when stroked,
 * the wheel passing through, and a self-centring grip that makes the stroke
 * grabbable. The grip is allowed precisely because it ALWAYS springs back to
 * centre on release — a handle that never stays put cannot be read as a
 * position, where both reverted attempts below drew shapes that did stay put.
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

/** The CSI slice used to observe cursor visibility without handling the mode. */
export interface TerminalModeParser {
  registerCsiHandler(
    id: { prefix?: string; intermediates?: string; final: string },
    callback: (params: (number | number[])[]) => boolean,
  ): { dispose(): void };
  registerEscHandler(
    id: { intermediates?: string; final: string },
    callback: () => boolean,
  ): { dispose(): void };
}

export interface LiveTuiCursorTracking {
  /** Reset local mode state before a terminal replay/reset boundary. */
  reset(): void;
  dispose(): void;
}

export const MIN_THUMB_PX = 28;
const MAX_RELAY_NOTCHES = 80;
const PAGE_UP = "\x1b[5~";
const PAGE_DOWN = "\x1b[6~";
const CURSOR_UP = "\x1b[A";
const CURSOR_DOWN = "\x1b[B";
const APPLICATION_CURSOR_UP = "\x1bOA";
const APPLICATION_CURSOR_DOWN = "\x1bOB";
const WHEEL_PIXELS_PER_CURSOR_ROW = 40;
const MAX_CURSOR_ROWS_PER_WHEEL = 12;

/**
 * Pixels of pointer travel per relayed unit when stroking an application rail.
 *
 * A stroke, not a jump to a position: the gesture says "keep going this way",
 * which is the only thing the rail can honestly promise when the application
 * owns the history. At 7 the maintainer's verdict on a real Claude pane was
 * "you drag and almost nothing happens" — a grip that moves the transcript
 * less than the hand moved feels dead, so the step errs brisk rather than
 * fine; the wheel remains the fine-grained gesture.
 */
export const STROKE_STEP_PX = 3;

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

function finiteWhole(value: unknown): number {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.floor(number)) : 0;
}

/** Which side can truthfully answer where this pane is scrolled. */
export function terminalScrollOwner(
  term: Terminal,
  liveTuiActive = false,
): TerminalScrollOwner {
  const tracking = term.modes?.mouseTrackingMode ?? "none";
  const bufferType = term.buffer?.active?.type ?? "normal";
  return liveTuiActive || bufferType === "alternate" || tracking !== "none"
    ? "application"
    : "terminal";
}

/** Read a scroll view without inspecting or interpreting terminal content. */
export function readTerminalScrollView(
  term: Terminal,
  liveTuiActive = false,
): TerminalScrollView {
  const owner = terminalScrollOwner(term, liveTuiActive);
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
 * Track cursor visibility as a candidate signal for a live drawn view.
 *
 * This is deliberately NOT an input-ownership decision: progress indicators
 * and disabled prompts also hide the cursor. A separate explicit user action
 * must activate cursor-key navigation while this candidate remains present.
 *
 * The handlers return false, so xterm still performs DECSET/DECRST itself. The
 * caller owns reset boundaries because `Terminal.reset()` is a local API call
 * and therefore does not pass through the parser.
 */
export function installLiveTuiCursorTracking(
  parser: TerminalModeParser,
  onActive: (active: boolean) => void,
): LiveTuiCursorTracking {
  let active = false;
  const update = (next: boolean) => {
    if (next === active) return;
    active = next;
    onActive(next);
  };
  const carriesCursorMode = (params: (number | number[])[]) =>
    params.some((param) =>
      Array.isArray(param) ? param.includes(25) : param === 25,
    );
  const handlers = [
    parser.registerCsiHandler({ prefix: "?", final: "l" }, (params) => {
      if (carriesCursorMode(params)) update(true);
      return false;
    }),
    parser.registerCsiHandler({ prefix: "?", final: "h" }, (params) => {
      if (carriesCursorMode(params)) update(false);
      return false;
    }),
    // DECSTR and RIS restore cursor visibility without emitting `?25h`.
    // Observe both while leaving xterm's reset implementation in charge.
    parser.registerCsiHandler({ intermediates: "!", final: "p" }, () => {
      update(false);
      return false;
    }),
    parser.registerEscHandler({ final: "c" }, () => {
      update(false);
      return false;
    }),
  ];
  return {
    reset: () => update(false),
    dispose: () => {
      update(false);
      for (const handler of handlers) handler.dispose();
    },
  };
}

/**
 * Move the selection/viewport of a keyboard-driven live TUI by whole rows.
 *
 * Inline coding-agent views are often drawn in the normal terminal buffer but
 * keep a windowed list of their own. Claude Code's workflow agent list is one
 * example: xterm can scroll the surrounding transcript, while the rows omitted
 * from that live list exist only inside Claude Code and are reached with cursor
 * keys. Use xterm's current cursor-key mode so the bytes match what a physical
 * ArrowUp/ArrowDown press would have produced.
 */
export function scrollLiveCursor(
  term: Terminal,
  direction: -1 | 1,
  rows = 1,
): number {
  const count = Math.min(
    MAX_CURSOR_ROWS_PER_WHEEL,
    Math.max(0, Math.floor(rows)),
  );
  if (count === 0) return 0;
  const applicationKeys = term.modes?.applicationCursorKeysMode === true;
  const sequence =
    direction < 0
      ? applicationKeys
        ? APPLICATION_CURSOR_UP
        : CURSOR_UP
      : applicationKeys
        ? APPLICATION_CURSOR_DOWN
        : CURSOR_DOWN;
  // One user action, one PTY/WebSocket frame. Repeating term.input here would
  // recreate the frame-count amplification that made long prompt delivery lag.
  term.input(sequence.repeat(count), true);
  return count;
}

/**
 * Give wheel input to an explicitly activated inline TUI view.
 *
 * Two histories can share a normal xterm buffer:
 *
 * * xterm's exact transcript, which keeps ordinary wheel behaviour; and
 * * a live, windowed block owned by the coding CLI, whose omitted rows are not
 *   terminal scrollback at all.
 *
 * `isLiveTuiActive` is an explicit user opt-in, not a guess derived from output
 * or cursor visibility. While it is active, both directions belong to the live
 * view. Shift+wheel is deliberately left native as the escape hatch to exact
 * terminal history. Plain prompts and every negotiated mouse protocol also
 * remain native.
 */
export function createLiveTuiWheelHandler(
  term: Terminal,
  isLiveTuiActive: () => boolean,
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

    const active = term.buffer?.active;
    const bufferType = active?.type ?? "normal";
    const tracking = term.modes?.mouseTrackingMode ?? "none";
    const direction: -1 | 1 = event.deltaY < 0 ? -1 : 1;
    const keyboardOwnedLiveView =
      bufferType === "normal" && tracking === "none" && isLiveTuiActive();

    if (!keyboardOwnedLiveView) {
      pixelRemainder = 0;
      return true;
    }

    let rows: number;
    if (event.deltaMode === WheelEvent.DOM_DELTA_PAGE) {
      rows = applicationPageNotches(term.rows);
      pixelRemainder = 0;
    } else if (event.deltaMode === WheelEvent.DOM_DELTA_LINE) {
      rows = Math.max(1, Math.ceil(Math.abs(event.deltaY)));
      pixelRemainder = 0;
    } else {
      if (pixelRemainder !== 0 && Math.sign(pixelRemainder) !== direction) {
        pixelRemainder = 0;
      }
      pixelRemainder += event.deltaY;
      rows = Math.floor(Math.abs(pixelRemainder) / WHEEL_PIXELS_PER_CURSOR_ROW);
      if (rows > 0) {
        pixelRemainder -= direction * rows * WHEEL_PIXELS_PER_CURSOR_ROW;
      }
    }

    if (rows > 0) scrollLiveCursor(term, direction, rows);
    // Even a sub-row trackpad delta belongs to the accumulator above. Letting
    // xterm also process it would emit a mouse report or move a second history.
    return false;
  };
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
