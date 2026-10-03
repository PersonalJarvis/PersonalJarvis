/**
 * The contract every retro game on the arcade floor implements.
 *
 * A game is pure state plus three functions: `create` a fresh run, `step` it
 * at a fixed rate with the buttons the player holds, and `draw` it onto a
 * small logical canvas (the cabinet overlay scales that up crisply). The
 * overlay (RetroArcadeOverlay) owns everything around the game: the keyboard
 * and gamepad, start / pause / game over, the best score, leaving the game.
 * So a game never touches the DOM, the clock or `Math.random` directly, and
 * its rules are unit-testable without a browser.
 */

/** The fixed simulation step in seconds; the overlay calls `step` this often. */
export const RETRO_STEP = 1 / 60;

/** The six buttons of the cabinet's control panel. */
export interface RetroButtons {
  left: boolean;
  right: boolean;
  up: boolean;
  down: boolean;
  /** Primary action: Space, J or gamepad A (fire, jump, launch, rotate). */
  a: boolean;
  /** Secondary action: Shift, K or gamepad B/X (bomb, hard drop, hold, boost). */
  b: boolean;
}

export interface RetroPointer {
  /** Position in logical canvas pixels. */
  x: number;
  y: number;
  /** The primary button is held. */
  down: boolean;
  /** It went down since the previous step (edge). */
  clicked: boolean;
}

export interface RetroInput extends RetroButtons {
  /** Buttons that went down since the previous step (edges); use these for one-shot actions like rotate or jump. */
  pressed: RetroButtons;
  /** The mouse / touch over the screen, for games that set `pointer: true`; null otherwise or when it is off-screen. */
  pointer: RetroPointer | null;
}

export interface RetroStatus {
  score: number;
  /** Lives or tries left, when the game has them. */
  lives?: number;
  level?: number;
  /** The run has ended (out of lives, crashed, topped out). The overlay shows game over and stores the best score. */
  over: boolean;
  /** The run ended by winning (the overlay says so instead of "game over"). */
  won?: boolean;
}

/** What `draw` may know besides the state. */
export interface RetroDrawInfo {
  /** Seconds since the overlay opened; for blinking and idle animation only, never for rules. */
  time: number;
  /** The person prefers reduced motion: no screen shake, no strobing, no flashing. */
  reduced: boolean;
  /** The run has not started yet (title screen) or is paused; the overlay draws its own text over it. */
  idle: boolean;
}

export interface RetroGame<S> {
  id: string;
  /** Logical canvas size in pixels (e.g. 320 × 240). Draw only inside it; the overlay scales it to fit. */
  width: number;
  height: number;
  /** A fresh run. `random` returns [0, 1); use it for every random choice so runs are reproducible in tests. */
  create(random: () => number): S;
  /** Advance by `dt` seconds (always RETRO_STEP from the overlay). Mutating `state` in place is expected. */
  step(state: S, input: RetroInput, dt: number, random: () => number): void;
  /** Paint the whole frame (clear it first). Canvas 2D only; no images, no external assets. */
  draw(ctx: CanvasRenderingContext2D, state: S, info: RetroDrawInfo): void;
  status(state: S): RetroStatus;
  /** The game reads `input.pointer` (mouse aim, clicks). */
  pointer?: boolean;
}

/** No button held, nothing pressed. */
export function idleInput(): RetroInput {
  const off = (): RetroButtons => ({ left: false, right: false, up: false, down: false, a: false, b: false });
  return { ...off(), pressed: off(), pointer: null };
}

/** The pixel face loaded app-wide (index.css); every game writes its text in it. */
export const RETRO_FONT = "'Pixelify Sans', ui-monospace, monospace";

/** A canvas font string in the arcade face, e.g. `retroFont(8)` or `retroFont(16, 600)`. */
export function retroFont(px: number, weight = 400): string {
  return `${weight} ${px}px ${RETRO_FONT}`;
}

/** A small seeded PRNG (mulberry32) for tests and attract screens; the overlay uses Math.random. */
export function seededRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
