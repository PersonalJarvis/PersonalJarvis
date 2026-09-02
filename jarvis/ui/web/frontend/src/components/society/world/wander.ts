/**
 * Idle life at zero token cost: the rest-biased wander model.
 *
 * MASTERPLAN §2.7/§2.8 — idle agents wander client-side, no LLM, no sync
 * between windows. The behaviour is re-implemented from the published
 * description of ambient pet AI (GameAIPro ch. 36 as cited by Hermes' roam
 * model; no code taken): most decision beats REST, and dwell times are
 * exponentially distributed so nothing ticks like a metronome.
 */

/** Share of decision beats that rest instead of walking. */
export const REST_PROBABILITY = 0.62;
/** Mean rest length in seconds; clamped to the band below. */
export const DWELL_MEAN_S = 4.2;
export const DWELL_MIN_S = 1.5;
export const DWELL_MAX_S = 13;
/** How long a working agent holds its desk pose before the next idle beat. */
export const WORK_BEAT_S = 6;

export type Beat = { kind: "rest"; seconds: number } | { kind: "walk" };

/** Exponential dwell with the documented mean, clamped so it reads as calm. */
export function dwellSeconds(rng: () => number): number {
  const u = Math.min(1 - 1e-9, Math.max(1e-9, rng()));
  const raw = -Math.log(1 - u) * DWELL_MEAN_S;
  return Math.min(DWELL_MAX_S, Math.max(DWELL_MIN_S, raw));
}

export function nextBeat(rng: () => number): Beat {
  if (rng() < REST_PROBABILITY) return { kind: "rest", seconds: dwellSeconds(rng) };
  return { kind: "walk" };
}

/** A small seeded PRNG so two windows wander differently but a test repeats. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function seedFromString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
