/**
 * Pure bookkeeping behind the trails: where a ribbon keeps its samples, when
 * the next footprint lands, how a ribbon turns into a flat strip on the floor.
 * Kept free of three.js so the rules are unit tested.
 */

export interface TrailSample { x: number; z: number; t: number }

/** Distance between two ribbon samples, metres. */
export const RIBBON_STEP_M = 0.12;
export const RIBBON_MAX_SAMPLES = 28;
/** Distance between two footprints, metres. */
export const STEP_LENGTH_M = 0.42;

/** Adds a sample once the mover is a step past the last one; drops the oldest beyond the cap. */
export function pushSample(samples: TrailSample[], x: number, z: number, t: number): boolean {
  const last = samples[samples.length - 1];
  if (last && Math.hypot(x - last.x, z - last.z) < RIBBON_STEP_M) return false;
  // A jump of several metres is a teleport (elevator, floor switch): the ribbon starts over.
  if (last && Math.hypot(x - last.x, z - last.z) > 2.5) samples.length = 0;
  samples.push({ x, z, t });
  if (samples.length > RIBBON_MAX_SAMPLES) samples.splice(0, samples.length - RIBBON_MAX_SAMPLES);
  return true;
}

export function pruneSamples(samples: TrailSample[], t: number, lifeS: number): void {
  let drop = 0;
  while (drop < samples.length && t - samples[drop].t > lifeS) drop++;
  if (drop > 0) samples.splice(0, drop);
}

/**
 * Writes a flat strip along the samples into `positions` (two vertices per
 * sample, at height `y`) and returns, per sample, how bright it is (1 at the
 * newest, 0 at the oldest or fully aged). The strip narrows towards its tail.
 */
export function writeRibbon(samples: readonly TrailSample[], t: number, lifeS: number, widthM: number, y: number,
  positions: Float32Array): number[] {
  const n = samples.length;
  const bright: number[] = [];
  for (let i = 0; i < n; i++) {
    const s = samples[i];
    const prev = samples[Math.max(0, i - 1)];
    const next = samples[Math.min(n - 1, i + 1)];
    let dx = next.x - prev.x, dz = next.z - prev.z;
    const len = Math.hypot(dx, dz) || 1;
    dx /= len; dz /= len;
    const along = n > 1 ? i / (n - 1) : 1;
    const age = Math.min(1, Math.max(0, (t - s.t) / lifeS));
    const half = (widthM / 2) * along * (1 - age * 0.5);
    // Perpendicular on the floor: (-dz, dx).
    positions[i * 6] = s.x - dz * half; positions[i * 6 + 1] = y; positions[i * 6 + 2] = s.z + dx * half;
    positions[i * 6 + 3] = s.x + dz * half; positions[i * 6 + 4] = y; positions[i * 6 + 5] = s.z - dx * half;
    bright.push(along * (1 - age));
  }
  return bright;
}

/** Footprints alternate left and right of the path, a hand's width apart. */
export function footprintAt(x: number, z: number, heading: number, left: boolean): { x: number; z: number; rot: number } {
  const side = left ? 1 : -1;
  // Right of heading h is (−cos h, sin h) for a +z-forward figure.
  return { x: x + Math.cos(heading) * 0.09 * side, z: z - Math.sin(heading) * 0.09 * side, rot: heading };
}

/** Hue (0..1) along a rainbow ribbon, cycling slowly with time. */
export function rainbowHue(along: number, t: number): number {
  const h = (along * 0.85 + t * 0.15) % 1;
  return h < 0 ? h + 1 : h;
}
