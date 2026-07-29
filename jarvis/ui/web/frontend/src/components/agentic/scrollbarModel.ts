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
 *   (Claude Code, Gemini, OpenCode and Kimi all take the alternate buffer and
 *   the mouse within milliseconds of starting) keeps its transcript in its own
 *   process, where nothing about it can be observed from the outside. The only
 *   true statements available are "you are at the live end" and "you have
 *   scrolled N lines away from it", so that is all the thumb says: it rests at
 *   the bottom on the newest output, climbs with the distance travelled, and
 *   assumes {@link ASSUMED_SCREENS} screens of history until the top has
 *   actually been measured, so there is always somewhere left to go.
 *
 * ## Nothing here reads the application's words
 *
 * An earlier generation anchored "you are at the live end" on Claude Code's
 * `Jump to bottom (ctrl+End)` overlay: absence of that string was taken as
 * proof of the live end. It broke twice over. Claude Code paints a SECOND
 * overlay once output arrives while the view is parked (`1 new message
 * (ctrl+End)`), so the regex missed and the bar snapped to the bottom on every
 * repaint of a pane that was in fact at the very top — and no other CLI ever
 * paints either string, so for all of them the count was unanchored forever.
 * That is AP-27 in a second place: a verdict gated on transcript CONTENT is a
 * verdict that fails for every wording and every language it did not foresee.
 *
 * What replaces it are two symmetric brakes that read only whether the screen
 * MOVED, never what it says:
 *
 * 1. **The top brake.** Up-notches whose repaint never arrives (the transcript
 *    region unchanged once the pty had time to answer) were ignored by the
 *    application: they are un-counted, and further ups are neither counted nor
 *    relayed until the screen moves again. The top stops being scrollable
 *    forever. The total travel is then KNOWN, so the thumb maps the real span
 *    ({@link Travel.ceiling}) — top means top.
 * 2. **The bottom brake.** Down-notches that likewise change nothing mean the
 *    newest output is already on screen: the count re-anchors at zero. This is
 *    the live-end anchor, and it works for a CLI in any language.
 *
 * Both brakes require having SEEN the transcript move earlier in the same
 * scroll-back episode ({@link Travel.moved}): a busy CLI can leave the screen
 * unchanged long past {@link SETTLE_MS} without any boundary being involved,
 * and a verdict measured from that once pinned a pane's thumb for good.
 * Judging nothing, a brake merely keeps the old estimate — this module fails
 * open by design.
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
 * been relayed so far, and the drag's release sends whatever a capped burst
 * still owes. Kept small also to bound how far a burst can overrun the
 * transcript's top before the top brake can see it.
 */
export const MAX_NOTCHES_PER_STEP = 30;

/**
 * Screens of history a `travel` pane assumes above an unmeasured transcript.
 *
 * Nothing can be read about a full-screen CLI's history until the top has been
 * hit, so this number alone decides how big the thumb looks on a pane at its
 * live end. One screen made the thumb fill HALF the track — a bar that size
 * claims the pane holds two screens in total, which no working agent ever
 * does, and it is what the maintainer reported as "far too big". Four keeps it
 * a fifth of the track: unmistakably a scrollbar, without inventing a history
 * length nobody measured. Once the top HAS been reached the real span replaces
 * it ({@link Travel.ceiling}).
 */
export const ASSUMED_SCREENS = 4;

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
 * The view of a pane whose application owns the screen.
 *
 * With the top never reached, the span is the distance travelled plus the
 * assumed headroom, so there is always somewhere left to go. Once the top HAS
 * been reached the total is known ({@link Travel.ceiling}) and the span is
 * that measurement — the thumb can then genuinely say "top".
 */
export function travelView(
  travelled: number,
  rows: number,
  ceiling: number | null = null,
): ScrollView {
  const back = Math.max(0, Math.round(travelled));
  const above =
    ceiling === null
      ? back + Math.max(1, rows) * ASSUMED_SCREENS
      : Math.max(back, ceiling);
  return { kind: "travel", above, back, rows };
}

// ---------------------------------------------------------------- travel

/**
 * How long the pty gets to answer a relayed notch before an unchanged screen
 * counts as proof the notch fell off an end. The CLI answers a notch in
 * 1–30 ms when idle (measured), but a CLI mid-task plus a loaded render
 * loop can stretch far past that — and a wrong verdict here once pinned a
 * pane's thumb to a false top (2026-07-28). Hence the generous window AND
 * the `moved` requirement in {@link screenTravel}.
 */
export const SETTLE_MS = 500;

/** What a full-screen CLI's pane knows about where it stands. */
export interface Travel {
  /** Best estimate of lines back from the live end. */
  travelled: number;
  /** Total travel measured at the transcript's top, once reached. */
  ceiling: number | null;
  /** Up-lines counted but not yet confirmed by a repaint. */
  pendingUp: number;
  /** When the newest unconfirmed up was counted. */
  lastUpAt: number;
  /** Down-lines counted but not yet confirmed by a repaint. */
  pendingDown: number;
  /** When the newest unconfirmed down was counted. */
  lastDownAt: number;
  /** The transcript region's last known content, for the brakes. */
  fingerprint: string | null;
  /** Ups are known to be falling off the top right now. */
  saturated: boolean;
  /**
   * The transcript has been SEEN to move during this scroll-back episode.
   *
   * Both brakes' precondition, and the fix for the pinned-thumb deadlock: a
   * busy CLI can leave the screen unchanged long past {@link SETTLE_MS}
   * without a single notch having fallen off an end. Genuinely reaching a
   * boundary requires having scrolled through content first, which repaints
   * — so an episode that never moved proves latency, not a boundary, and
   * must neither saturate nor measure a ceiling nor claim the live end.
   */
  moved: boolean;
}

export function freshTravel(): Travel {
  return {
    travelled: 0,
    ceiling: null,
    pendingUp: 0,
    lastUpAt: 0,
    pendingDown: 0,
    lastDownAt: 0,
    fingerprint: null,
    saturated: false,
    moved: false,
  };
}

/** What one look at the screen saw — see {@link readScreen} in the component. */
export interface ScreenGlance {
  /** Content of the transcript region, excluding the self-animating chrome. */
  fingerprint: string;
}

/**
 * The count after one observed wheel event, real or relayed alike.
 *
 * `notches` is signed the way a wheel's `deltaY` is — POSITIVE towards the
 * live end (the hand pulled towards itself), negative into the history — and
 * counts the mouse reports the terminal will actually send, not the DOM
 * events. One physical wheel turn is several reports, and counting it as one
 * was a systematic five-fold under-count of every real scroll.
 */
export function wheelTravel(
  travel: Travel,
  notches: number,
  now: number,
): Travel {
  if (!Number.isFinite(notches) || Math.trunc(notches) === 0) return travel;
  const lines = Math.abs(Math.trunc(notches)) * LINES_PER_NOTCH;
  if (notches > 0) {
    // Towards the live end: a down always has somewhere to go while the
    // count is positive, and leaving the top ends the saturation. The lines
    // stay pending until a repaint confirms them — an unanswered down is how
    // the bottom brake learns the newest output is already on screen.
    return {
      ...travel,
      travelled: Math.max(0, travel.travelled - lines),
      pendingUp: 0,
      saturated: false,
      pendingDown: travel.pendingDown + lines,
      lastDownAt: now,
    };
  }
  // Away from the live end. While saturated these are known to be ignored by
  // the application — counting them is exactly the inflation this fixes.
  if (travel.saturated) return travel;
  return {
    ...travel,
    travelled: travel.travelled + lines,
    pendingUp: travel.pendingUp + lines,
    lastUpAt: now,
    pendingDown: 0,
  };
}

/**
 * Reconcile the count with what the screen actually shows.
 *
 * `glance` is null when the screen could not be read — the estimate then
 * simply stands, which is the fail-open this module owes its history.
 */
export function screenTravel(
  travel: Travel,
  glance: ScreenGlance | null,
  now: number,
): Travel {
  if (!glance) return travel;

  if (travel.fingerprint !== glance.fingerprint) {
    // The transcript moved: whatever was pending has been answered. Only a
    // CHANGE proves movement — the episode's first look merely records.
    return {
      ...travel,
      fingerprint: glance.fingerprint,
      pendingUp: 0,
      pendingDown: 0,
      saturated: false,
      moved: travel.moved || travel.fingerprint !== null,
    };
  }

  // The screen stood still. Whichever direction was asked for and not
  // answered has run into that end of the transcript — provided this episode
  // has scrolled at all (see Travel.moved).
  if (travel.pendingUp > 0 && now - travel.lastUpAt >= SETTLE_MS) {
    // Counted ups, an answered pty, an unmoved screen: those notches did
    // nothing, so they leave the count either way. But they only prove a TOP
    // when this episode has scrolled before — an episode that never moved is
    // a busy CLI, and a ceiling measured from it pinned a pane's thumb to a
    // false top with every further relay refused (2026-07-28).
    const travelled = Math.max(0, travel.travelled - travel.pendingUp);
    return travel.moved
      ? {
          ...travel,
          travelled,
          pendingUp: 0,
          saturated: true,
          ceiling: Math.max(travelled, LINES_PER_NOTCH),
        }
      : { ...travel, travelled, pendingUp: 0 };
  }

  if (travel.pendingDown > 0 && now - travel.lastDownAt >= SETTLE_MS) {
    if (!travel.moved) return { ...travel, pendingDown: 0 };
    // Downs the application had nothing left to answer with: the newest
    // output is on screen. This is the live-end anchor — the one that used to
    // be a hardcoded English string — and it also closes the episode, so the
    // next one has to prove movement afresh.
    return {
      ...travel,
      travelled: 0,
      pendingDown: 0,
      saturated: false,
      fingerprint: null,
      moved: false,
    };
  }

  return travel;
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
