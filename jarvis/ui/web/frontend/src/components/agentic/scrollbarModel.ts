/**
 * The arithmetic behind a pane's drag scrollbar — no DOM, no xterm, no timers.
 *
 * A pane runs one of two kinds of program, and the bar has to be honest about
 * which one it is standing on:
 *
 * * **`exact`** — the terminal owns the history. A CLI that prints line by
 *   line (Codex, a plain shell) leaves everything in xterm's scrollback, and
 *   every number here is read straight off that buffer. The thumb is a map.
 * * **`travel`** — the application owns the screen. A full-screen CLI
 *   (Claude Code takes the alternate buffer and the mouse within milliseconds
 *   of starting) keeps its transcript in its own process, where nothing about
 *   it can be observed from the outside. The only true statements available
 *   are "you are at the live end" and "you have scrolled N lines away from
 *   it", so that is all the thumb says: it rests at the bottom on the newest
 *   output, climbs with the distance travelled, and always leaves one
 *   screenful of assumed headroom so it never claims a top nobody verified.
 *   Whenever the user returns to the live end the count re-anchors at zero,
 *   so the one position that matters is always right.
 *
 * An earlier generation of this feature tried to MEASURE the second kind by
 * nudging the application and comparing screens, and every unreadable screen
 * came out as "nothing to scroll". Nothing in this module measures anything.
 */

/** Lines one relayed wheel notch moves a full-screen CLI (measured: ~3). */
export const LINES_PER_NOTCH = 3;

/** A thumb shorter than this cannot be grabbed in a tall pane. */
export const MIN_THUMB_PX = 24;

/**
 * The most notches one drag step may relay at once.
 *
 * A fast drag across a long history asks for hundreds of lines in a single
 * pointer move. The application answers each notch individually, so a burst
 * without a ceiling floods the pty with mouse reports it will spend seconds
 * working through — long after the hand has stopped. The next move sends the
 * remainder anyway, because each step is computed against what has actually
 * been relayed so far.
 */
export const MAX_NOTCHES_PER_STEP = 90;

/** What a pane can scroll right now, in lines. */
export interface ScrollView {
  /** How the numbers were obtained — see the file header. */
  kind: "exact" | "travel";
  /** Lines of history above the current screen. */
  above: number;
  /** Lines the view stands back from the live end; 0 = newest output. */
  back: number;
  /** Lines on screen at once. */
  rows: number;
}

/** Thumb box inside a track of known height, in px from the track's top. */
export interface ThumbBox {
  topPx: number;
  heightPx: number;
}

/**
 * Clamp with a hard rule about garbage: these values come from pointer
 * coordinates and third-party terminal fields, and a single NaN that gets
 * through moves a pane to nowhere. Anything non-finite becomes the low bound.
 */
function clamp(value: number, low: number, high: number): number {
  if (!Number.isFinite(value)) return low;
  return value < low ? low : value > high ? high : value;
}

/**
 * The view of a pane whose terminal owns the scrollback — xterm's own numbers.
 *
 * `length` is the buffer's total line count, `baseY` the top line of the
 * newest screen, `viewportY` the top line currently shown.
 */
export function exactView(
  length: number,
  baseY: number,
  viewportY: number,
  rows: number,
): ScrollView {
  const above = Math.max(0, length - rows);
  return {
    kind: "exact",
    above,
    back: clamp(baseY - viewportY, 0, above),
    rows,
  };
}

/**
 * The view of a pane whose application owns the screen: the distance
 * travelled from the live end, plus one screenful of assumed headroom.
 */
export function travelView(travelled: number, rows: number): ScrollView {
  const back = Math.max(0, Math.round(travelled));
  return { kind: "travel", above: back + rows, back, rows };
}

/** Is there anywhere for this pane to go? */
export function hasScroll(view: ScrollView | null): boolean {
  return Boolean(view && view.above > 0 && view.rows > 0);
}

/** Where the thumb sits in a `trackPx`-tall track; null when there is no bar. */
export function thumbBox(
  view: ScrollView | null,
  trackPx: number,
): ThumbBox | null {
  if (!view || !hasScroll(view) || trackPx <= 0) return null;
  const total = view.above + view.rows;
  const heightPx = clamp(
    Math.round((trackPx * view.rows) / total),
    Math.min(MIN_THUMB_PX, trackPx),
    trackPx,
  );
  // 0 back = live end = thumb at the very bottom of the track.
  const away = clamp(view.back / view.above, 0, 1);
  return { heightPx, topPx: Math.round((trackPx - heightPx) * (1 - away)) };
}

/** How far back a thumb whose top edge is at `topPx` is asking to be. */
export function backAtThumbTop(
  topPx: number,
  trackPx: number,
  view: ScrollView | null,
): number {
  const box = thumbBox(view, trackPx);
  if (!box || !view) return 0;
  const travel = trackPx - box.heightPx;
  if (travel <= 0) return view.back;
  const away = 1 - clamp(topPx / travel, 0, 1);
  return Math.round(away * view.above);
}

/**
 * Whole notches that carry an application `lines` further back (negative:
 * towards the live end), capped per step — see {@link MAX_NOTCHES_PER_STEP}.
 */
export function notchesFor(lines: number): number {
  const whole = Math.trunc(lines / LINES_PER_NOTCH);
  return clamp(whole, -MAX_NOTCHES_PER_STEP, MAX_NOTCHES_PER_STEP);
}

/**
 * The travel count after one observed wheel event.
 *
 * Counted per event rather than per delta pixel: the application scrolls per
 * received notch, and both a gentle and a violent turn of a real wheel arrive
 * as one event per notch. The count can only be an estimate either way — what
 * keeps it honest is the clamp at zero, where it re-anchors against the one
 * position the application also stops at.
 */
export function countWheel(travelled: number, deltaY: number): number {
  if (!Number.isFinite(deltaY) || deltaY === 0) return travelled;
  const step = deltaY < 0 ? LINES_PER_NOTCH : -LINES_PER_NOTCH;
  return Math.max(0, travelled + step);
}
