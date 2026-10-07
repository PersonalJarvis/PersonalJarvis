/**
 * The fixed-step clock behind the cabinet overlay.
 *
 * Animation frames arrive at whatever rate the display runs (60, 120, 144 Hz,
 * or a stutter after a busy moment), but a retro game's rules must advance in
 * equal steps so it plays the same everywhere. The accumulator collects real
 * time and hands out whole steps; after a long stall (a hidden window, a GC
 * pause) it drops the backlog instead of fast-forwarding the game.
 */
import { RETRO_STEP } from "./retroGame";

/** At most this many steps per animation frame; anything beyond is a stall and is dropped. */
export const MAX_STEPS_PER_FRAME = 5;

export interface RetroClock {
  /** Real seconds collected but not yet spent on a step. */
  acc: number;
}

export function createRetroClock(): RetroClock {
  return { acc: 0 };
}

/**
 * Add `elapsed` real seconds and return how many fixed steps to run now.
 * Negative or non-finite time (a clock jump) counts as nothing.
 */
export function stepsDue(clock: RetroClock, elapsed: number, step = RETRO_STEP, maxSteps = MAX_STEPS_PER_FRAME): number {
  if (Number.isFinite(elapsed) && elapsed > 0) clock.acc += elapsed;
  // A hair of tolerance so 1/60 s frames never alternate between 0 and 2 steps from rounding.
  const due = Math.floor((clock.acc + 1e-9) / step);
  if (due > maxSteps) {
    clock.acc = 0;
    return maxSteps;
  }
  clock.acc = Math.max(0, clock.acc - due * step);
  return due;
}

/** Forget collected time, e.g. when a run starts or resumes, so it does not begin with a burst. */
export function resetClock(clock: RetroClock): void {
  clock.acc = 0;
}
