/**
 * The locomotion rules that keep a figure from walking backwards, sliding or
 * snapping — docs/agent-society/character-pipeline.md §6, as pure functions.
 * The R3F walker reads these and holds no math of its own;
 * `walkerKinematics.test.ts` pins them.
 */

/** Below this speed a figure keeps its last heading (§6.2). */
export const MOVING_EPS_MPS = 0.05;
/** Nominal biped walking speed: a 1.28 m stride over a 1.0 s clip (§6.3). */
export const NOMINAL_WALK_MPS = 1.28;
/** Wander walks calmer than a purposeful move (§6.3). */
export const WANDER_SPEED_MPS = NOMINAL_WALK_MPS * 0.8;
/** Maximum yaw rate for a biped, 540 °/s (§6.2). */
export const TURN_RATE_RAD_S = (540 * Math.PI) / 180;
/** Arrival: decelerate over the last 0.4 m (§6.2). */
export const ARRIVE_SLOW_M = 0.4;
/** A target more than 120° behind is a reversal: turn in place first (§6.2). */
export const REVERSAL_RAD = (120 * Math.PI) / 180;
export const REVERSAL_HOLD_S = 0.15;

/**
 * Heading for a ground velocity. A figure faces +Z in its own space, and
 * `rotation.y = θ` maps local +Z to world (sin θ, 0, cos θ), hence
 * atan2(vx, vz) — not atan2(vz, vx).
 */
export function headingFor(vx: number, vz: number): number {
  return Math.atan2(vx, vz);
}

/** The signed shortest rotation from `from` to `to`, in (−π, π]. */
export function shortestArc(from: number, to: number): number {
  let d = (to - from) % (2 * Math.PI);
  if (d > Math.PI) d -= 2 * Math.PI;
  if (d <= -Math.PI) d += 2 * Math.PI;
  return d;
}

/**
 * Turn `current` toward `target` by at most `maxStep` radians along the
 * shortest arc, with an ease-out over the last stretch so the stop is soft.
 */
export function turnToward(current: number, target: number, maxStep: number): number {
  const d = shortestArc(current, target);
  if (Math.abs(d) <= maxStep) return target;
  const eased = Math.min(maxStep, Math.abs(d) * 0.55 + maxStep * 0.45);
  return current + Math.sign(d) * eased;
}

export function isMoving(vx: number, vz: number): boolean {
  return Math.hypot(vx, vz) > MOVING_EPS_MPS;
}

/** Speed factor for the remaining distance to the final waypoint. */
export function arrivalFactor(remainingM: number): number {
  if (remainingM >= ARRIVE_SLOW_M) return 1;
  return Math.max(0.35, remainingM / ARRIVE_SLOW_M);
}

export interface StepResult {
  x: number;
  z: number;
  /** Index of the waypoint being approached after this step. */
  index: number;
  /** Ground velocity over this step, m/s. */
  vx: number;
  vz: number;
  /** True once the final waypoint is reached. */
  arrived: boolean;
}

/**
 * Advance along a polyline of waypoints for `dt` seconds at `speed`, passing
 * through corners without stopping, decelerating into the last point. The
 * figure never overshoots the final waypoint.
 */
export function stepAlong(
  x: number,
  z: number,
  waypoints: ReadonlyArray<[number, number]>,
  index: number,
  speed: number,
  dt: number,
): StepResult {
  if (index >= waypoints.length) return { x, z, index, vx: 0, vz: 0, arrived: true };
  const startX = x;
  const startZ = z;
  const last = waypoints[waypoints.length - 1];
  const remainingToEnd = Math.hypot(last[0] - x, last[1] - z);
  let budget = speed * arrivalFactor(remainingToEnd) * dt;
  let i = index;
  while (budget > 0 && i < waypoints.length) {
    const [wx, wz] = waypoints[i];
    const dx = wx - x;
    const dz = wz - z;
    const dist = Math.hypot(dx, dz);
    if (dist <= budget) {
      x = wx;
      z = wz;
      budget -= dist;
      i++;
    } else {
      x += (dx / dist) * budget;
      z += (dz / dist) * budget;
      budget = 0;
    }
  }
  const arrived = i >= waypoints.length;
  const vx = dt > 0 ? (x - startX) / dt : 0;
  const vz = dt > 0 ? (z - startZ) / dt : 0;
  return { x, z, index: i, vx, vz, arrived };
}

/**
 * Two walkers sharing a tile stand a little apart: a deterministic lateral
 * offset keyed by the agent id (§6.4), ±0.3 m, never simulated avoidance.
 */
export function lateralOffset(agentId: string): number {
  let h = 0;
  for (let i = 0; i < agentId.length; i++) h = (h * 31 + agentId.charCodeAt(i)) | 0;
  return (((h >>> 0) % 1000) / 1000 - 0.5) * 0.6;
}
