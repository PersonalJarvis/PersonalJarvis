/**
 * What a terminal pane can scroll right now, and where its scrollbar sits.
 *
 * ## Why this is not just CSS on the xterm viewport
 *
 * The obvious way to give a pane a scrollbar is to style `.xterm-viewport` and
 * let the browser draw the native one. That works for a CLI which prints line
 * by line — Codex does, and its bar behaved perfectly. It does nothing at all
 * for Claude Code, and the reason is a property of the CLI rather than a bug in
 * the styling: measured against Claude Code 2.1.220, the moment it opens a real
 * session it emits
 *
 *     ESC[?1049h   switch to the ALTERNATE screen buffer
 *     ESC[?1000h ESC[?1002h ESC[?1003h ESC[?1006h   full SGR mouse tracking
 *
 * In the alternate buffer a terminal has no scrollback by definition — the
 * application owns the whole screen and keeps its own history, exactly like
 * `vim` or `less`. So `.xterm-viewport` has nothing to scroll, the native thumb
 * fills its track, and no amount of CSS can move it. Meanwhile the mouse
 * tracking means the wheel is no longer the terminal's to handle: xterm
 * forwards it and Claude Code scrolls its own transcript.
 *
 * ## The mouse, not the buffer, is what decides
 *
 * The alternate buffer is the loud symptom, but it is not the cause, and gating
 * on it alone was wrong. A CLI may keep its transcript to itself while staying
 * on the NORMAL buffer — measured against Claude Code 2.1.195 running here, the
 * viewport reported `scrollHeight 286 / clientHeight 242 / scrollTop 44`: a
 * handful of stale lines behind a live screen, permanently pinned to the end.
 * That is the worst of both worlds, because it looks like honest scrollback:
 * the bar drew a thumb filling 85% of its track and parked it at the bottom,
 * announcing "you are at the end" to somebody reading the middle of Claude
 * Code's own history.
 *
 * What actually settles it is mouse tracking. Once a CLI asks for the mouse,
 * xterm hands it every wheel event and stops scrolling its viewport at all
 * (`Terminal._bindMouse`: the viewport only sees a wheel the application did
 * not take). From that moment the viewport is frozen furniture in whatever
 * buffer it happens to be, its position says nothing about what the user is
 * looking at, and any proportional thumb drawn from it is a lie. So the check
 * below reads the mouse FIRST and the buffer type second.
 *
 * Hence two sources for one and the same picture:
 *
 * * `scrollback` — the terminal holds the history and the wheel (Codex, a plain
 *   shell). Position and extent are read straight off xterm's buffer.
 * * `app` — the application took the mouse, so it holds the history (Claude
 *   Code). Position and extent are MEASURED from how far the screen's content
 *   moves when it is scrolled — see ./paneAppScroll.
 * * `none` — nothing to scroll (a fresh pane, or a full-screen app measured as
 *   having no history at all). The bar stays away instead of drawing a dead
 *   track.
 *
 * ## Both modes draw the same thumb, because both now know the same things
 *
 * `app` mode used to draw a fixed grip centred in the track instead of a
 * position, on the reasoning that a shape which never moves cannot lie. It
 * lied anyway — just in a language the code was not reading. Every scrollbar
 * anyone has ever used says "you are here" with exactly that shape, so a pane
 * parked at the live end of Claude Code's transcript showed a marking halfway
 * up its track and was read, correctly by the only grammar available, as "you
 * are halfway up" (reported 2026-07-27). Abstaining from a claim is not the
 * same as saying nothing when the shape you abstain with is the shape the
 * claim is made in.
 *
 * The fix was not a different shape but a real answer: ./paneAppScroll measures
 * the application's position from its own screen, so `app` mode has a genuine
 * line count to report and the geometry below is shared, unconditionally. The
 * only thing still specific to `app` mode is HOW a drag is carried out —
 * relayed wheel notches instead of a viewport call — because that is the only
 * language the application listens in.
 */
import type { Terminal } from "@xterm/xterm";
import {
  appScrollExtent,
  appTakesWheel,
  AT_LIVE_END,
  type AppScrollPosition,
} from "./paneAppScroll";

export { appTakesWheel };

/** Which of the two scrolling worlds a pane is in — see the file header. */
export type PaneScrollMode = "scrollback" | "app" | "none";

export interface PaneScrollState {
  mode: PaneScrollMode;
  /** Buffer lines in total, scrollback included. */
  total: number;
  /** Lines visible at once. */
  rows: number;
  /** Buffer line currently at the top of the viewport. */
  top: number;
}

export interface ThumbGeometry {
  topPx: number;
  heightPx: number;
}

export const IDLE_STATE: PaneScrollState = {
  mode: "none",
  total: 0,
  rows: 0,
  top: 0,
};

/** A thumb shorter than this is impossible to grab in a tall pane. */
export const MIN_THUMB_PX = 26;

function clamp(value: number, low: number, high: number): number {
  return value < low ? low : value > high ? high : value;
}

/**
 * Read a pane's scroll situation off the live terminal.
 *
 * `position` is what ./paneAppScroll has measured of an application that holds
 * its own history; it is ignored for a pane whose terminal holds the scrollback
 * itself, where xterm's buffer is the better answer.
 *
 * Defensive by design: this runs against whatever xterm build the app was
 * bundled with, and a missing field must degrade to "nothing to scroll" rather
 * than throw inside a render.
 */
export function readScrollState(
  term: Terminal | null,
  position: AppScrollPosition = AT_LIVE_END,
): PaneScrollState {
  const buffer = term?.buffer?.active;
  const rows = term?.rows ?? 0;
  if (!term || !buffer || rows < 1) return IDLE_STATE;

  const total = buffer.length ?? rows;
  const top = buffer.viewportY ?? 0;

  // The mouse first — see the file header. An application holding the wheel
  // holds the history with it, in EITHER buffer, so the viewport it left behind
  // is ignored in favour of what the measurement found.
  if (appTakesWheel(term)) {
    const extent = appScrollExtent(position, rows);
    // A measurement that found no history at all — a dashboard, a pager on a
    // short file — takes the bar away rather than drawing a thumb that fills
    // its own track.
    if (extent.total <= rows) return IDLE_STATE;
    return { mode: "app", total: extent.total, rows, top: extent.top };
  }

  // Alternate buffer without the mouse: the application draws the whole screen
  // and there is nothing to relay a wheel notch to — the escape sequence would
  // land in its input as if it had been typed.
  if (buffer.type === "alternate") return IDLE_STATE;

  if (total > rows) return { mode: "scrollback", total, rows, top };
  return IDLE_STATE;
}

/** Where to draw the thumb inside a track of `trackPx`, or null for no bar. */
export function thumbGeometry(
  state: PaneScrollState,
  trackPx: number,
): ThumbGeometry | null {
  if (trackPx <= 0) return null;
  if (state.mode === "none" || state.total <= state.rows) return null;

  const heightPx = Math.min(
    trackPx,
    Math.max(MIN_THUMB_PX, Math.round((trackPx * state.rows) / state.total)),
  );
  const maxTop = state.total - state.rows;
  const progress = maxTop > 0 ? clamp(state.top / maxTop, 0, 1) : 0;
  return { heightPx, topPx: Math.round((trackPx - heightPx) * progress) };
}

/** The buffer line a thumb dragged to `topPx` is asking for. */
export function lineForThumbTop(
  topPx: number,
  trackPx: number,
  state: PaneScrollState,
): number {
  const geometry = thumbGeometry(state, trackPx);
  if (!geometry) return state.top;
  const span = trackPx - geometry.heightPx;
  const maxTop = state.total - state.rows;
  if (span <= 0 || maxTop <= 0) return 0;
  return Math.round(clamp(topPx / span, 0, 1) * maxTop);
}

/**
 * Hand one wheel notch to the application running in `host`.
 *
 * Synthesized as a real `wheel` event on xterm's screen element rather than
 * written to the socket by hand, deliberately: xterm already knows which mouse
 * protocol the application negotiated (X10, VT200, SGR, …) and encodes the
 * report accordingly. Writing the bytes ourselves would mean guessing, and a
 * guess wrong by one protocol arrives in the CLI's prompt as garbage.
 *
 * Emitted in LINE mode with a delta of exactly one, which is also how
 * ./paneAppScroll recognises its own relays and learns what a notch is worth.
 *
 * `direction` is +1 to scroll down (towards newer output), -1 for up.
 */
export function relayWheelNotch(
  host: HTMLElement | null,
  direction: 1 | -1,
): boolean {
  if (!host || typeof WheelEvent === "undefined") return false;
  const screen = host.querySelector<HTMLElement>(".xterm-screen");
  if (!screen) return false;
  const rect = screen.getBoundingClientRect();
  return screen.dispatchEvent(
    new WheelEvent("wheel", {
      bubbles: true,
      cancelable: true,
      clientX: rect.left + rect.width / 2,
      clientY: rect.top + rect.height / 2,
      // Lines, not pixels: xterm divides a pixel delta by the row height and
      // carries the remainder, so a synthetic pixel delta can round to zero
      // lines and be dropped entirely.
      deltaMode: 1,
      deltaY: direction,
    }),
  );
}
