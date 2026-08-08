/**
 * Provider-neutral terminal scrolling primitives.
 *
 * A terminal has one of two scroll owners:
 *
 * * xterm owns a normal buffer, so its exact line and extent are public;
 * * a full-screen application owns an alternate/mouse-tracking buffer, so the
 *   only truthful operation is to relay scroll input to that application.
 *
 * The second case cannot be read, so it is MEASURED instead. Every unit of
 * scroll this surface relays is counted, and the two ends announce themselves:
 * a relay that leaves the visible screen unchanged is an end, because an
 * application that could still scroll would have repainted. Reaching the older
 * end therefore calibrates the whole travel, and reaching the newer end pins
 * the offset back to zero, so both ends are exact and the distance between them
 * is a bounded estimate rather than a guess.
 *
 * The distinction that matters against the earlier failed attempt: nothing here
 * READS the application's screen. It compares two screens for equality and asks
 * only "did this move", which no CLI has to opt into and no CLI can break by
 * rewording its own output. Until the older end has been touched once the rail
 * still says so (`ApplicationScrollEstimate.calibrated`).
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

/** A measured application position, in the relay units this surface sends. */
export interface ApplicationScrollEstimate {
  /** Distance from the newest end. Zero means pinned to the bottom. */
  offset: number;
  /** Travel between the two ends; the working span while uncalibrated. */
  span: number;
  /** True once the older end has answered, which fixes `span` exactly. */
  calibrated: boolean;
  atTop: boolean;
  atBottom: boolean;
}

export const MIN_THUMB_PX = 28;
const MAX_RELAY_NOTCHES = 80;
const PAGE_UP = "\x1b[5~";
const PAGE_DOWN = "\x1b[6~";

/**
 * How much of the screen a relay must repaint before it counts as movement.
 *
 * Deliberately far above the noise floor: coding CLIs redraw a spinner, an
 * elapsed timer or a status line every second or so, which is one or two rows.
 * Real movement repaints nearly everything.
 */
const MOVED_ROW_RATIO = 0.25;

/**
 * The travel assumed before the older end has ever answered.
 *
 * Something has to be assumed or the thumb could not move at all on the first
 * gesture. Roughly a handful of screens: large enough that a short scroll does
 * not slam the thumb to the top, small enough to stay in the right half of the
 * rail. The first contact with either end replaces it with a measurement.
 */
const PROVISIONAL_SPAN = 40;

/** Fallback cell height when the pane has not been measured yet. */
const FALLBACK_CELL_PX = 18;

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

export interface ThumbOptions {
  /** Measured application position; omit for the uncontrolled centred grip. */
  estimate?: ApplicationScrollEstimate | null;
  minThumbPx?: number;
}

/** Height of the application grip, which no measurement can size truthfully. */
function applicationGripHeight(track: number, minThumbPx: number): number {
  return clamp(track * 0.18, Math.min(minThumbPx, track), Math.min(64, track));
}

/**
 * Geometry for either an exact thumb or the measured application grip.
 */
export function scrollThumbGeometry(
  view: TerminalScrollView,
  trackPx: number,
  { estimate, minThumbPx = MIN_THUMB_PX }: ThumbOptions = {},
): ScrollThumbGeometry {
  const track = Math.max(0, trackPx);
  if (track === 0) return { top: 0, height: 0 };

  if (view.owner === "application") {
    const height = applicationGripHeight(track, minThumbPx);
    const travel = Math.max(0, track - height);
    if (!estimate || estimate.span <= 0) return { top: travel / 2, height };
    // Offset counts backwards from the newest end, so a zero offset belongs at
    // the bottom of the rail — the same place an exact thumb sits when a
    // terminal is scrolled fully down.
    const reach = clamp(estimate.offset / estimate.span, 0, 1);
    return { top: travel * (1 - reach), height };
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

/** Map a dragged application grip back to the offset it is pointing at. */
export function applicationOffsetAtThumbTop(
  estimate: ApplicationScrollEstimate,
  topPx: number,
  trackPx: number,
  minThumbPx = MIN_THUMB_PX,
): number {
  const track = Math.max(0, trackPx);
  if (track === 0 || estimate.span <= 0) return estimate.offset;
  const travel = Math.max(0, track - applicationGripHeight(track, minThumbPx));
  if (travel === 0) return estimate.offset;
  const reach = 1 - clamp(topPx, 0, travel) / travel;
  return Math.round(estimate.span * reach);
}

/**
 * The visible rows, as plain strings, for comparing one screen against another.
 *
 * Never inspected for meaning — only compared — so this stays true for a CLI
 * this project has never seen.
 */
export function screenSignature(term: Terminal): string[] {
  const active = term.buffer?.active;
  if (!active?.getLine) return [];
  const rows = Math.max(1, finiteWhole(term.rows));
  const top = finiteWhole(active.viewportY);
  const lines: string[] = [];
  for (let index = 0; index < rows; index += 1) {
    lines.push(active.getLine(top + index)?.translateToString(true) ?? "");
  }
  return lines;
}

/** Did the screen really move, or did a status line just tick over? */
export function screenMoved(
  before: readonly string[],
  after: readonly string[],
  ratio = MOVED_ROW_RATIO,
): boolean {
  const rows = Math.max(before.length, after.length);
  if (rows === 0) return false;
  let changed = 0;
  for (let index = 0; index < rows; index += 1) {
    if ((before[index] ?? "") !== (after[index] ?? "")) changed += 1;
  }
  return changed / rows >= ratio;
}

/**
 * How many relay units a wheel event is worth.
 *
 * xterm converts a wheel into whole rows before reporting it to the
 * application, so rows are the unit this surface counts in — including for the
 * wheels the user rolls over the terminal itself, which never pass through
 * `scrollApplication`.
 */
export function wheelNotches(
  term: Terminal,
  host: HTMLElement | null,
  event: WheelEvent,
): number {
  const rows = Math.max(1, finiteWhole(term.rows));
  const magnitude = Math.abs(event.deltaY);
  if (magnitude === 0) return 0;
  if (typeof WheelEvent !== "undefined" && event.deltaMode === WheelEvent.DOM_DELTA_PAGE) {
    return Math.max(1, Math.round(magnitude * rows));
  }
  if (typeof WheelEvent !== "undefined" && event.deltaMode === WheelEvent.DOM_DELTA_LINE) {
    return Math.max(1, Math.round(magnitude));
  }
  const screen = host?.querySelector<HTMLElement>(".xterm-screen");
  const measured = screen ? screen.getBoundingClientRect().height / rows : 0;
  const cell = measured > 1 ? measured : FALLBACK_CELL_PX;
  return Math.max(1, Math.round(magnitude / cell));
}

/**
 * The measured position of an application-owned screen.
 *
 * Pure state: the caller decides when a relay has settled and whether the
 * screen moved, which keeps every timer out of this module and every terminal
 * out of the tests.
 */
export class ApplicationScrollTracker {
  private offset = 0;
  private span = 0;
  private calibrated = false;

  /** Forget everything — a new process owns a screen this has never seen. */
  reset(): void {
    this.offset = 0;
    this.span = 0;
    this.calibrated = false;
  }

  estimate(): ApplicationScrollEstimate {
    // Uncalibrated, the working span keeps a margin above the distance already
    // travelled. Without it the thumb would pin itself to the top the moment
    // the user scrolls further than the assumption, and stay there while the
    // screen is still moving.
    const span = this.calibrated
      ? this.span
      : Math.max(Math.ceil(this.offset * 1.2), PROVISIONAL_SPAN);
    return {
      offset: Math.min(this.offset, span),
      span,
      calibrated: this.calibrated,
      atTop: this.calibrated && this.span > 0 && this.offset >= this.span,
      atBottom: this.offset === 0,
    };
  }

  /**
   * Book a relay the moment it is sent, before the application has answered.
   *
   * The thumb has to follow the hand during a drag, and a drag asks for an
   * absolute offset — both need the counted position to be current rather than
   * one settle-window behind. `settle` takes it back if the screen stayed put.
   */
  advance(direction: -1 | 1, notches: number): void {
    const count = Math.max(0, Math.floor(notches));
    if (count === 0) return;
    this.offset = Math.max(0, this.offset - direction * count);
    this.span = Math.max(this.span, this.offset);
  }

  /**
   * Close a settled run of relays.
   *
   * `moved` false is the load-bearing case: the application was asked to scroll
   * and did not repaint, so it has nothing left that way. Older means the
   * distance counted so far IS the whole travel; newer means the offset is
   * zero. Either way the advance booked for that run was fiction.
   */
  settle(direction: -1 | 1, notches: number, moved: boolean): void {
    if (moved) return;
    const count = Math.max(0, Math.floor(notches));
    if (direction < 0) {
      this.offset = Math.max(0, this.offset - count);
      this.span = this.offset;
      this.calibrated = this.offset > 0;
    } else {
      this.offset = 0;
    }
  }
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
