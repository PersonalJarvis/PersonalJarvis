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
 * ## The three anchors that keep the count honest
 *
 * Pure counting drifts, and the drift was visible in the product within a day:
 * notches relayed at the transcript's TOP are silently ignored by the CLI but
 * were still counted, so the claimed position inflated — and after scrolling
 * back down the thumb hung mid-track at the live end. Probed against the real
 * CLI (2026-07-28, hidden pty): an ignored notch emits ZERO bytes, so the
 * stream cannot tell. What CAN tell, and what the count is anchored to:
 *
 * 1. **The scrolled-back overlay.** Claude Code paints "Jump to bottom
 *    (ctrl+End)" onto the screen whenever the view left the live end, and
 *    erases it there. Once a pane has seen that overlay ONCE (capability
 *    learned, not provider-gated), its absence is proof of "at the newest
 *    output" and snaps the count to zero — the anchor that ends thumb-stuck-
 *    mid-track. Its presence floors the count at one notch.
 * 2. **The saturation brake.** Up-notches whose repaint never arrives (the
 *    transcript region above the overlay unchanged once the pty had time to
 *    answer) were ignored by the CLI: they are un-counted, and further ups are
 *    neither counted nor relayed until the screen moves again. The top stops
 *    being scrollable-forever.
 * 3. **The measured ceiling.** The moment the brake engages, the total travel
 *    to the top is KNOWN, and the thumb maps the real span — top means top.
 *
 * An application that paints no overlay keeps the plain counted behaviour.
 * An earlier generation of this feature tried to MEASURE the second kind by
 * nudging the application and comparing screens as its ONLY source of truth,
 * and every unreadable screen came out as "nothing to scroll". The brake
 * fails the other way: judging nothing, it merely keeps the old estimate.
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
 * been relayed so far. Kept small also to bound how far a burst can overrun
 * the transcript's top before the saturation brake can see it.
 */
export const MAX_NOTCHES_PER_STEP = 30;

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
 * With the top never reached, the span is the distance travelled plus one
 * screenful of assumed headroom, so there is always somewhere left to go.
 * Once the top HAS been reached the total is known ({@link Travel.ceiling})
 * and the span is that measurement — the thumb can then genuinely say "top".
 */
export function travelView(
  travelled: number,
  rows: number,
  ceiling: number | null = null,
): ScrollView {
  const back = Math.max(0, Math.round(travelled));
  const above = ceiling === null ? back + rows : Math.max(back, ceiling);
  return { kind: "travel", above, back, rows };
}

// ---------------------------------------------------------------- travel

/**
 * How long the pty gets to answer an up-notch before an unchanged screen
 * counts as proof the notch fell off the top. The CLI answers a notch in
 * 1–30 ms when idle (measured), but a CLI mid-task plus a loaded render
 * loop can stretch far past that — and a wrong verdict here once pinned a
 * pane's thumb to a false top (2026-07-28). Hence the generous window AND
 * the `moved` requirement in {@link screenTravel}.
 */
export const SETTLE_MS = 500;

/**
 * Overlays a full-screen CLI paints while its view is away from the live
 * end. Matched against the visible screen; the row that matches also splits
 * the transcript (above it) from the animated chrome (below it), which is
 * why the fingerprint in {@link screenTravel} covers only the rows above.
 */
export const SCROLLED_BACK_MARKERS = [/Jump to bottom/i];

/** What a full-screen CLI's pane knows about where it stands. */
export interface Travel {
  /** Best estimate of lines back from the live end. */
  travelled: number;
  /** This application paints a scrolled-back overlay — learned, never assumed. */
  markerSeen: boolean;
  /** Total travel measured at the transcript's top, once reached. */
  ceiling: number | null;
  /** Up-lines counted but not yet confirmed by a repaint. */
  pendingUp: number;
  /** When the newest unconfirmed up was counted. */
  lastUpAt: number;
  /** The transcript region's last known content, for the brake. */
  fingerprint: string | null;
  /** Ups are known to be falling off the top right now. */
  saturated: boolean;
  /**
   * The transcript has been SEEN to move during this scroll-back episode.
   *
   * The brake's precondition, and the fix for the pinned-thumb deadlock: a
   * busy CLI can leave the screen unchanged long past {@link SETTLE_MS}
   * without a single notch having fallen off the top. Genuinely reaching
   * the top requires having scrolled through content first, which repaints
   * — so an episode that never moved proves latency, not a boundary, and
   * must neither saturate nor measure a ceiling.
   */
  moved: boolean;
}

export function freshTravel(): Travel {
  return {
    travelled: 0,
    markerSeen: false,
    ceiling: null,
    pendingUp: 0,
    lastUpAt: 0,
    fingerprint: null,
    saturated: false,
    moved: false,
  };
}

/** What one look at the screen saw — see {@link readScreen} in the component. */
export interface ScreenGlance {
  /** Index of the scrolled-back overlay's row, or -1 when absent. */
  markerRow: number;
  /** Content of the transcript region above the overlay ("" without one). */
  fingerprint: string;
}

/** The count after one observed wheel event (real or relayed alike). */
export function wheelTravel(travel: Travel, deltaY: number, now: number): Travel {
  if (!Number.isFinite(deltaY) || deltaY === 0) return travel;
  if (deltaY > 0) {
    // Towards the live end: a down always has somewhere to go while the
    // count is positive, and leaving the top ends the saturation.
    return {
      ...travel,
      travelled: Math.max(0, travel.travelled - LINES_PER_NOTCH),
      pendingUp: 0,
      saturated: false,
    };
  }
  // Away from the live end. While saturated these are known to be ignored by
  // the application — counting them is exactly the inflation this fixes.
  if (travel.saturated) return travel;
  return {
    ...travel,
    travelled: travel.travelled + LINES_PER_NOTCH,
    pendingUp: travel.pendingUp + LINES_PER_NOTCH,
    lastUpAt: now,
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

  if (glance.markerRow < 0) {
    // No overlay. From an application known to paint one, that is proof of
    // the live end — the anchor that ends "thumb stuck mid-track". It also
    // closes the scroll-back episode: the next one proves movement afresh.
    if (!travel.markerSeen) return travel;
    if (
      travel.travelled === 0 &&
      travel.pendingUp === 0 &&
      !travel.saturated &&
      !travel.moved
    ) {
      return travel;
    }
    return {
      ...travel,
      travelled: 0,
      pendingUp: 0,
      saturated: false,
      fingerprint: null,
      moved: false,
    };
  }

  let next: Travel = travel.markerSeen
    ? travel
    : { ...travel, markerSeen: true };

  if (next.fingerprint !== glance.fingerprint) {
    // The transcript moved: whatever was pending has been answered. Only a
    // CHANGE proves movement — the episode's first look merely records.
    next = {
      ...next,
      fingerprint: glance.fingerprint,
      pendingUp: 0,
      saturated: false,
      moved: next.moved || next.fingerprint !== null,
    };
  } else if (next.pendingUp > 0 && now - next.lastUpAt >= SETTLE_MS) {
    // Counted ups, an answered pty, an unmoved screen: those notches did
    // nothing, so they leave the count either way. But they only prove a
    // TOP when this episode has scrolled before — an episode that never
    // moved is a busy CLI, and a ceiling measured from it pinned a pane's
    // thumb to a false top with every further relay refused (2026-07-28).
    const travelled = Math.max(0, next.travelled - next.pendingUp);
    next = next.moved
      ? {
          ...next,
          travelled,
          pendingUp: 0,
          saturated: true,
          ceiling: Math.max(travelled, LINES_PER_NOTCH),
        }
      : { ...next, travelled, pendingUp: 0 };
  }

  // The overlay itself says the view is away from the live end — a count of
  // zero would draw the thumb at a bottom the application denies.
  if (next.travelled === 0) {
    next = { ...next, travelled: LINES_PER_NOTCH };
  }
  return next;
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

