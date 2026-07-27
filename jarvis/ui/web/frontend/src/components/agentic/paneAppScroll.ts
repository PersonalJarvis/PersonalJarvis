/**
 * Where a full-screen CLI sits in ITS OWN history — measured, not guessed.
 *
 * ## The problem this exists to solve
 *
 * A CLI that takes the mouse (Claude Code and every other Ink-style agent UI)
 * keeps its transcript to itself: the terminal's buffer holds one screen, the
 * wheel is forwarded to the application, and nothing xterm can be asked reveals
 * how far back the user has scrolled. See ./paneScroll for the full measurement.
 *
 * The first answer to that was to draw no position at all — a bar spanning the
 * whole track with a small marking parked in its middle, meaning "there is no
 * position here". It did not read that way. The full-track bar is drawn faintly
 * by design, so the marking is the only part anyone actually sees, and a short
 * bright shape halfway up a track says exactly one thing in the only grammar
 * scrollbars have: *you are in the middle*. Reported 2026-07-27 by a pane
 * sitting at the live end of its transcript. Refusing to answer with the shape
 * an answer is given in is not refusing to answer.
 *
 * ## So the position is measured, from the one thing that is observable
 *
 * The application will not tell us where it is, but it will SHOW us: scroll it
 * and the lines on screen move. That movement is the measurement.
 *
 * Around every burst of wheel events the screen is snapshotted before and after,
 * and {@link detectShift} recovers how far the content travelled by finding the
 * row offset that lines the two snapshots up. From there:
 *
 * * content moved **down** → the user went back into history; the distance from
 *   the live end grows by exactly that many lines,
 * * content did **not move** although a scroll was asked for → an end of the
 *   history was reached, which is how both the top (the extent becomes known)
 *   and the bottom (the distance is truly zero) are pinned down exactly,
 * * how far one wheel notch moves the application is itself learned from the
 *   same measurement, so dragging the thumb can aim.
 *
 * Nothing here is assumed about which CLI is running, what it draws, or what its
 * keys do. It is the same technique a screen reader uses to follow a TUI, and it
 * works on any application that scrolls its own output.
 *
 * ## What it is honest about
 *
 * Before anyone has scrolled, NOTHING about the history is known — not its
 * extent and not whether it exists at all — and a bar is drawn only once
 * something has actually been measured ({@link hasMeasuredHistory}).
 *
 * That rule replaces an assumption. An untouched pane used to be credited with
 * "at least one more screen above", which put a half-track thumb at the bottom
 * of the track — and the other half of the track was then empty furniture down
 * the side of every pane that nobody had scrolled yet, which is every pane in a
 * fresh workspace (reported 2026-07-27, second round on the same strip). The
 * assumed screenful was not measured, so the empty half was paying for a claim
 * this module had no evidence for.
 *
 * Once a wheel notch HAS been measured the extent is real, and the arithmetic
 * below applies as before: while the top is still unfound one more screen is
 * assumed above the deepest point reached, which is a claim about size only —
 * openly made, corrected by the next measurement, and never a claim about
 * position. A full-screen application with no history at all (a dashboard, a
 * pager on a short file) reveals itself on the first scroll attempt that moves
 * nothing, and its pane keeps no bar either.
 */
import type { Terminal } from "@xterm/xterm";

/**
 * True when the running application receives wheel events itself.
 *
 * The one test that decides which of the two worlds a pane is in — see
 * ./paneScroll, which re-exports this. It lives here because everything on this
 * side of the line depends on it, and a shared module may not import back into
 * the one that imports it.
 */
export function appTakesWheel(term: Terminal | null): boolean {
  const tracking = term?.modes?.mouseTrackingMode;
  return Boolean(tracking) && tracking !== "none";
}

export interface AppScrollPosition {
  /** Lines between the top of the screen and the live end. 0 = newest output. */
  offset: number;
  /** How much history is known to exist above the live end, in lines. */
  span: number;
  /**
   * True once a scroll towards older output moved nothing: `span` is then the
   * real top of the transcript rather than a lower bound.
   */
  spanKnown: boolean;
  /** Lines the application moves per wheel notch, as measured. */
  linesPerNotch: number;
}

/** What a pane is before anybody has scrolled it: showing the newest output. */
export const AT_LIVE_END: AppScrollPosition = {
  offset: 0,
  span: 0,
  spanKnown: false,
  linesPerNotch: 1,
};

/**
 * Has anything about this pane's history actually been measured yet?
 *
 * The gate between "we know nothing" and "we know something", and the reason a
 * fresh pane draws no bar at all — see the file header. Any one of the three is
 * enough: the user is standing away from the live end, a depth has been reached
 * at some point, or a scroll towards older output ran into the top.
 *
 * Deliberately a property of the VALUE rather than an identity check against
 * {@link AT_LIVE_END}. A position that travelled back into the history and
 * returned to the newest output is no longer that constant, but it has learned
 * that the history exists — and a pane the user has already scrolled once must
 * keep the bar they scrolled it with.
 */
export function hasMeasuredHistory(position: AppScrollPosition): boolean {
  return position.spanKnown || position.span > 0 || position.offset > 0;
}

/**
 * Fewest recognisable rows two screens need before they may be compared.
 *
 * A near-empty screen matches itself at every offset, so a shift measured off
 * one would be noise presented as a position.
 */
export const MIN_COMPARABLE_ROWS = 4;

/** Longest jump one notch may be credited with — a guard against a bad match. */
export const MAX_LINES_PER_NOTCH = 12;

function clamp(value: number, low: number, high: number): number {
  return value < low ? low : value > high ? high : value;
}

/** Rows carrying enough text to identify themselves again after a scroll. */
function identifiable(text: string): boolean {
  return text.trim().length > 2;
}

/**
 * The lines currently on screen, top row first.
 *
 * Defensive like the rest of this area: an xterm build without `getLine`, or a
 * pane whose terminal is not up yet, yields an empty screen rather than throwing
 * inside an event handler.
 */
export function visibleRows(term: Terminal | null): string[] {
  const buffer = term?.buffer?.active;
  const rows = term?.rows ?? 0;
  if (!term || !buffer || rows < 1 || typeof buffer.getLine !== "function") {
    return [];
  }
  const base = buffer.viewportY ?? 0;
  const lines: string[] = [];
  for (let i = 0; i < rows; i += 1) {
    const line = buffer.getLine(base + i);
    lines.push(line ? line.translateToString(true).trimEnd() : "");
  }
  return lines;
}

/**
 * How far the screen's content travelled between two snapshots.
 *
 * Positive means the content moved DOWN the screen — older lines appeared above
 * it — which is what scrolling back through a history looks like. Null means the
 * two screens have too little in common to compare, which happens when a pane
 * repaints completely or scrolls further than one screenful at a time.
 *
 * The offset that lines up the most rows wins, with a tie going to the smallest
 * movement, so a screen where only a spinner changed reads as "did not move"
 * rather than as an arbitrary shift. Rows an application pins to the bottom —
 * the prompt box and status line of a coding agent — match at zero and nowhere
 * else, so they raise every candidate's floor equally and never decide the
 * outcome as long as the scrolling region is the larger part of the screen.
 */
export function detectShift(before: string[], after: string[]): number | null {
  const anchors = before.filter(identifiable).length;
  if (before.length < MIN_COMPARABLE_ROWS || anchors < MIN_COMPARABLE_ROWS) {
    return null;
  }

  const reach = Math.max(before.length, after.length) - 1;
  let best = 0;
  let bestScore = -1;
  for (let shift = -reach; shift <= reach; shift += 1) {
    let score = 0;
    for (let i = 0; i < before.length; i += 1) {
      const j = i + shift;
      if (j < 0 || j >= after.length) continue;
      if (identifiable(before[i]) && before[i] === after[j]) score += 1;
    }
    if (
      score > bestScore ||
      (score === bestScore && Math.abs(shift) < Math.abs(best))
    ) {
      best = shift;
      bestScore = score;
    }
  }
  return bestScore >= MIN_COMPARABLE_ROWS ? best : null;
}

/**
 * Fold one burst of scrolling into a position.
 *
 * `intent` is the wheel direction that was asked for, in `deltaY` signs —
 * positive towards newer output. `observed` is what {@link detectShift} saw, or
 * null when the screens could not be compared; an unmeasurable burst falls back
 * to what was SENT, and deliberately claims no end of the history, because
 * "nothing moved" and "we could not tell" must never collapse into each other.
 */
export function applyShift(
  position: AppScrollPosition,
  observed: number | null,
  intent: number,
): AppScrollPosition {
  const towardsOlder = intent < 0;
  const moved = observed ?? -intent * position.linesPerNotch;

  let offset = Math.max(0, position.offset + moved);
  let span = position.span;
  let spanKnown = position.spanKnown;
  let linesPerNotch = position.linesPerNotch;

  if (observed !== null && intent !== 0) {
    if (observed === 0) {
      // A scroll that moved nothing is an end of the history — the only exact
      // fix this measurement ever gets, and worth more than any accumulated sum.
      if (towardsOlder) {
        span = offset;
        spanKnown = true;
      } else {
        offset = 0;
      }
    } else if (Math.abs(observed) >= Math.abs(intent)) {
      // Only an unclamped move says anything about a notch's size: one that ran
      // into the end of the history moved less than the application would have.
      linesPerNotch = clamp(
        Math.round(Math.abs(observed) / Math.abs(intent)),
        1,
        MAX_LINES_PER_NOTCH,
      );
    }
  }

  // Scrolling past a top we thought we knew means we never knew it — the
  // transcript grew, or the earlier reading was wrong.
  if (offset > span) {
    span = offset;
    if (observed === null || observed !== 0 || !towardsOlder) spanKnown = false;
  }

  return { offset, span, spanKnown, linesPerNotch };
}

/**
 * The buffer arithmetic a scrollbar needs for a measured position.
 *
 * Returns the numbers a plain scrollback pane would report — total lines and
 * the line at the top of the viewport — so both kinds of pane draw their thumb
 * through exactly the same geometry.
 */
export function appScrollExtent(
  position: AppScrollPosition,
  rows: number,
): { total: number; top: number } {
  const reached = Math.max(position.span, position.offset);
  // While the top is unfound, one more screenful is assumed to exist above:
  // enough for the thumb to have somewhere to travel, never enough to pretend
  // the user is anywhere but where they are.
  const span = position.spanKnown ? reached : reached + rows;
  return { total: span + rows, top: span - Math.min(position.offset, span) };
}

/** Wheel notches that move an application `lines` towards newer output. */
export function notchesForLines(lines: number, linesPerNotch: number): number {
  return Math.round(lines / Math.max(1, linesPerNotch));
}

/** Quiet time after the last wheel event before the screen is re-measured. */
export const SETTLE_MS = 120;

/**
 * How long a probe stays one notch back before it returns.
 *
 * One settle period plus a margin: the two notches must land in SEPARATE bursts
 * or {@link trackAppScroll} folds them into one intent of zero and measures
 * nothing at all.
 */
export const PROBE_RETURN_MS = SETTLE_MS + 60;

/**
 * Quiet time a pane must have before it is probed at all.
 *
 * Longer than one settle period on purpose. A user who is turning the wheel is
 * already measuring the pane far better than a probe could, and two sources of
 * movement folded into one burst measure neither — so the probe waits for the
 * tracker to fall silent, by which time a real scroll has answered the question
 * and no probe is sent.
 */
export const PROBE_WAIT_MS = SETTLE_MS + 40;

/**
 * Ask a full-screen CLI whether it has a history — without moving the user.
 *
 * The measurement above only ever happened as a side effect of somebody turning
 * the wheel, and that left the scrollbar unreachable in exactly the panes it was
 * written for. A CLI that holds its own history draws NO bar until something has
 * been measured (see {@link hasMeasuredHistory}, and the reasoning in the file
 * header) — so reaching for the right edge of an untouched Claude Code pane
 * revealed nothing, and the only way to make the bar appear was the wheel, which
 * is the one input that makes the bar unnecessary. Reported 2026-07-27, third
 * round on this strip, and the first two rounds are why the empty bar is not an
 * option: the fix cannot be "draw something anyway".
 *
 * So the question gets asked instead of waited for. One notch towards older
 * output, one settle period for the tracker to see what the screen did, one notch
 * back — a round trip the user does not end up anywhere different from, and after
 * which the answer is real: either a history exists and the bar can describe it,
 * or the screen did not move and the pane honestly keeps no bar.
 *
 * `relay` hands over one notch in the direction given (-1 towards older output),
 * i.e. `relayWheelNotch` in ./paneScroll. Returns a teardown that brings the
 * application back immediately if the probe is abandoned early — a pane left one
 * line into its history because the pointer moved away would be the probe
 * showing through.
 */
export function probeAppHistory(
  relay: (direction: 1 | -1) => void,
): () => void {
  let returned = false;
  const comeBack = () => {
    if (returned) return;
    returned = true;
    relay(1);
  };

  relay(-1);
  const timer = window.setTimeout(comeBack, PROBE_RETURN_MS);
  return () => {
    window.clearTimeout(timer);
    comeBack();
  };
}

/**
 * Longest a burst may run before it is measured anyway.
 *
 * A held-down scroll would otherwise move the screen further than one screenful
 * between snapshots, leaving the two with nothing in common and the measurement
 * with nothing to work from.
 */
export const BURST_MS = 260;

export interface AppScrollTrackerOptions {
  /** The xterm host every wheel event for this pane passes through. */
  host: HTMLElement;
  getTerminal: () => Terminal | null;
  onChange: (next: (previous: AppScrollPosition) => AppScrollPosition) => void;
}

/**
 * Watch a pane's wheel traffic and keep its measured position up to date.
 *
 * Returns the teardown. Attached in the capture phase so it sees the wheel
 * whether the user turned it over the terminal or the scrollbar relayed it, and
 * passively so it can never interfere with the event xterm is about to encode
 * for the application.
 */
export function trackAppScroll({
  host,
  getTerminal,
  onChange,
}: AppScrollTrackerOptions): () => void {
  let before: string[] | null = null;
  let intent = 0;
  let notchesOnly = true;
  let quiet: number | undefined;
  let deadline: number | undefined;

  const settle = () => {
    if (quiet !== undefined) window.clearTimeout(quiet);
    if (deadline !== undefined) window.clearTimeout(deadline);
    quiet = undefined;
    deadline = undefined;

    const snapshot = before;
    const asked = intent;
    const synthetic = notchesOnly;
    before = null;
    intent = 0;
    notchesOnly = true;
    if (!snapshot || asked === 0) return;

    const observed = detectShift(snapshot, visibleRows(getTerminal()));
    onChange((previous) => {
      const next = applyShift(previous, observed, asked);
      // A notch's size may only be learned from notches we sent ourselves: a
      // mouse wheel reports whatever its driver feels like, so counting its
      // events as notches would teach the drag the wrong step.
      return synthetic
        ? next
        : { ...next, linesPerNotch: previous.linesPerNotch };
    });
  };

  const onWheel = (event: WheelEvent) => {
    const term = getTerminal();
    // A pane whose terminal owns its scrollback needs none of this: its
    // viewport already reports the truth.
    if (!appTakesWheel(term) || event.deltaY === 0) return;
    if (!before) before = visibleRows(term);
    intent += event.deltaY > 0 ? 1 : -1;
    // Our own relays are emitted as exactly one line in line mode — see
    // `relayWheelNotch` in ./paneScroll.
    if (event.deltaMode !== 1 || Math.abs(event.deltaY) !== 1) {
      notchesOnly = false;
    }
    if (quiet !== undefined) window.clearTimeout(quiet);
    quiet = window.setTimeout(settle, SETTLE_MS);
    if (deadline === undefined) deadline = window.setTimeout(settle, BURST_MS);
  };

  host.addEventListener("wheel", onWheel, { capture: true, passive: true });
  return () => {
    if (quiet !== undefined) window.clearTimeout(quiet);
    if (deadline !== undefined) window.clearTimeout(deadline);
    host.removeEventListener("wheel", onWheel, { capture: true });
  };
}
