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
 * Hence two modes, and a scrollbar that reads which one it is in:
 *
 * * `scrollback` — the terminal holds the history and the wheel (Codex, a plain
 *   shell). The thumb is proportional and exact, and dragging it moves the
 *   viewport.
 * * `app` — the application took the mouse, so it holds the history (Claude
 *   Code). There is no honest position to draw, so the bar becomes a centred
 *   grip: dragging it relays wheel notches to the CLI, which scrolls itself. It
 *   springs back rather than pretending to know where it is.
 * * `none` — nothing to scroll (a fresh pane, or a full-screen app that does
 *   not take the mouse). The bar stays away instead of drawing a dead track.
 */
import type { Terminal } from "@xterm/xterm";

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

/** Height of the `app`-mode grip. Fixed: it encodes no position. */
export const GRIP_PX = 44;

/** How far the `app`-mode grip may travel from centre while dragged. */
export const GRIP_TRAVEL_PX = 60;

/** Drag distance that relays one wheel notch in `app` mode. */
export const JOG_STEP_PX = 12;

function clamp(value: number, low: number, high: number): number {
  return value < low ? low : value > high ? high : value;
}

/**
 * Read a pane's scroll situation off the live terminal.
 *
 * Defensive by design: this runs against whatever xterm build the app was
 * bundled with, and a missing field must degrade to "nothing to scroll" rather
 * than throw inside a render.
 */
export function readScrollState(term: Terminal | null): PaneScrollState {
  const buffer = term?.buffer?.active;
  const rows = term?.rows ?? 0;
  if (!term || !buffer || rows < 1) return IDLE_STATE;

  const total = buffer.length ?? rows;
  const top = buffer.viewportY ?? 0;

  // The mouse first — see the file header. An application holding the wheel
  // holds the history with it, in EITHER buffer, and the viewport it left
  // behind must not be drawn as though it were that history.
  if (appTakesWheel(term)) return { mode: "app", total: rows, rows, top: 0 };

  // Alternate buffer without the mouse: the application draws the whole screen
  // and there is nothing to relay a wheel notch to — the escape sequence would
  // land in its input as if it had been typed.
  if (buffer.type === "alternate") return IDLE_STATE;

  if (total > rows) return { mode: "scrollback", total, rows, top };
  return IDLE_STATE;
}

/** True when the running application receives wheel events itself. */
export function appTakesWheel(term: Terminal | null): boolean {
  const tracking = term?.modes?.mouseTrackingMode;
  return Boolean(tracking) && tracking !== "none";
}

/**
 * Where to draw the thumb inside a track of `trackPx`, or null for no bar.
 *
 * In `app` mode `offsetPx` is the live drag offset, so the grip follows the
 * pointer while it is held and returns to the middle when it is let go.
 */
export function thumbGeometry(
  state: PaneScrollState,
  trackPx: number,
  offsetPx = 0,
): ThumbGeometry | null {
  if (trackPx <= 0) return null;

  if (state.mode === "app") {
    const heightPx = Math.min(GRIP_PX, trackPx);
    const centre = (trackPx - heightPx) / 2;
    const travel = clamp(offsetPx, -GRIP_TRAVEL_PX, GRIP_TRAVEL_PX);
    return {
      heightPx,
      topPx: Math.round(clamp(centre + travel, 0, trackPx - heightPx)),
    };
  }

  if (state.mode !== "scrollback" || state.total <= state.rows) return null;

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
  if (!geometry || state.mode !== "scrollback") return state.top;
  const span = trackPx - geometry.heightPx;
  const maxTop = state.total - state.rows;
  if (span <= 0 || maxTop <= 0) return 0;
  return Math.round(clamp(topPx / span, 0, 1) * maxTop);
}

/** How many wheel notches a drag of `deltaPx` is worth in `app` mode. */
export function jogNotches(deltaPx: number): number {
  return Math.trunc(deltaPx / JOG_STEP_PX);
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
