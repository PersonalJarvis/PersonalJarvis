/**
 * What a pane can scroll, and where its scrollbar draws the thumb.
 *
 * Pure arithmetic — no DOM, no terminal, no timers — so the part that decides
 * what the user SEES can be reasoned about on its own.
 *
 * ## Two kinds of pane, one shape of answer
 *
 * * `buffer` — the terminal holds the history and the wheel: Codex, a plain
 *   shell. Everything is known exactly, straight off xterm's buffer.
 * * `app` — the application took the whole screen, so it holds its history and
 *   answers the wheel itself: Claude Code, and any other full-screen TUI.
 *   Nothing about that history is observable from outside.
 *
 * ## What an app-held pane claims, and why it is not a lie
 *
 * A previous version of this feature tried to MEASURE the second kind — nudge
 * the application by a wheel notch, compare the screen before and after, and
 * derive a real position from how far the content travelled. It worked in a
 * test and failed in the product for four rounds straight, because every way
 * that measurement can come back empty (a screen too alike to compare, a nudge
 * that arrives while the agent is repainting) was indistinguishable from "this
 * pane has nothing to scroll" — and the bar simply never appeared.
 *
 * So this one measures nothing. It counts the notches that have been sent and
 * assumes the history goes at least one screen further than wherever you have
 * got to ({@link appScroll}). That yields a thumb which:
 *
 * * sits at the bottom when you are at the newest output — the one position
 *   that matters, and the one the application agrees on, because both it and
 *   the counter below stop at the same end,
 * * climbs as you scroll back and returns as you scroll forward,
 * * never claims to have reached the top of a history nobody can see.
 *
 * It is an indicator of travel rather than a map of the transcript. That is
 * the honest thing to be when the transcript belongs to somebody else.
 */

/** Lines one wheel notch moves a full-screen application. */
export const LINES_PER_NOTCH = 3;

/** A thumb shorter than this cannot be grabbed in a tall pane. */
export const MIN_THUMB_PX = 28;

export interface PaneScroll {
  /** Lines of history above the current screen. */
  span: number;
  /** Lines the pane stands back from the newest output; 0 is the live end. */
  back: number;
  /** Lines on screen at once. */
  rows: number;
}

export interface ThumbGeometry {
  topPx: number;
  heightPx: number;
}

function clamp(value: number, low: number, high: number): number {
  // A non-finite input is treated as the low end rather than passed through:
  // these numbers come from pointer positions and terminal fields, and one NaN
  // reaching a scroll call moves a pane to nowhere.
  if (!Number.isFinite(value)) return low;
  return value < low ? low : value > high ? high : value;
}

/**
 * What a pane whose terminal owns the scrollback can scroll.
 *
 * `total` is the buffer's length, `base` the line the newest screen starts at,
 * and `viewport` the line currently at the top of it — xterm's own numbers.
 */
export function bufferScroll(
  total: number,
  base: number,
  viewport: number,
  rows: number,
): PaneScroll {
  const span = Math.max(0, total - rows);
  return { span, back: clamp(base - viewport, 0, span), rows };
}

/**
 * What a pane whose APPLICATION owns the screen claims to be able to scroll.
 *
 * One screenful further than wherever the user has travelled, so the thumb
 * always has somewhere left to go — see the file header for why this is a
 * claim about size only, never about position.
 */
export function appScroll(back: number, rows: number): PaneScroll {
  const travelled = Math.max(0, back);
  return { span: travelled + rows, back: travelled, rows };
}

/** Is there anywhere for this pane to scroll to? */
export function scrollable(scroll: PaneScroll | null): boolean {
  return Boolean(scroll && scroll.span > 0 && scroll.rows > 0);
}

/** Where the thumb sits in a track of `trackPx`, or null when there is no bar. */
export function thumbGeometry(
  scroll: PaneScroll | null,
  trackPx: number,
): ThumbGeometry | null {
  if (!scrollable(scroll) || trackPx <= 0) return null;
  const { span, back, rows } = scroll as PaneScroll;
  const total = span + rows;
  const heightPx = clamp(
    Math.round((trackPx * rows) / total),
    Math.min(MIN_THUMB_PX, trackPx),
    trackPx,
  );
  // 1 is the newest output, at the bottom of the track, where an untouched
  // pane stands.
  const progress = 1 - clamp(back / span, 0, 1);
  return { heightPx, topPx: Math.round((trackPx - heightPx) * progress) };
}

/** How far back a thumb dragged to `topPx` is asking to go, in lines. */
export function backForThumbTop(
  topPx: number,
  trackPx: number,
  scroll: PaneScroll | null,
): number {
  const geometry = thumbGeometry(scroll, trackPx);
  if (!geometry || !scroll) return 0;
  const travel = trackPx - geometry.heightPx;
  if (travel <= 0) return scroll.back;
  const progress = clamp(topPx / travel, 0, 1);
  return Math.round((1 - progress) * scroll.span);
}

/** Whole wheel notches that carry an application `lines` towards older output. */
export function notchesForLines(lines: number): number {
  return Math.trunc(lines / LINES_PER_NOTCH);
}
