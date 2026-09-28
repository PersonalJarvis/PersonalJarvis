/**
 * Walking a figure along a waypoint path at a fixed speed, turning smoothly
 * towards the direction of travel. Pure: callers own the Mover and the clock.
 */
import type { Point } from "./officeLayout";

/** Metres per second. */
export const WALK_SPEED = 1.35;
export const RUN_SPEED = 3.2;
/** Maximum turn rate in radians per second. */
export const TURN_RATE = 10;

export interface Mover {
  x: number;
  z: number;
  /** Rotation about +y; 0 = the figure faces +z. */
  heading: number;
  /** Remaining waypoints; the first one is the next target. */
  path: Point[];
}

const TAU = Math.PI * 2;

/** Wrap an angle into (-π, π]. */
export function wrapAngle(angle: number): number {
  let a = angle % TAU;
  if (a <= -Math.PI) a += TAU;
  else if (a > Math.PI) a -= TAU;
  return a;
}

/** Turn `current` towards `target` along the shorter way by at most `maxStep` radians. */
export function turnToward(current: number, target: number, maxStep: number): number {
  const diff = wrapAngle(target - current);
  if (Math.abs(diff) <= maxStep) return wrapAngle(target);
  return wrapAngle(current + Math.sign(diff) * maxStep);
}

/**
 * Advance the mover by speed × dt metres along its path, consuming reached
 * waypoints. Mutates `m`. `arrived` is true once the path is empty.
 */
export function stepMover(m: Mover, speed: number, dt: number): { moved: number; arrived: boolean } {
  let budget = Math.max(0, speed * dt);
  let moved = 0;
  let dirX = 0, dirZ = 0;
  while (m.path.length > 0 && budget > 0) {
    const next = m.path[0];
    const dx = next.x - m.x, dz = next.z - m.z;
    const dist = Math.hypot(dx, dz);
    if (dist <= budget) {
      m.x = next.x; m.z = next.z;
      budget -= dist; moved += dist;
      m.path.shift();
    } else {
      m.x += (dx / dist) * budget; m.z += (dz / dist) * budget;
      moved += budget; budget = 0;
    }
    if (dist > 1e-9) { dirX = dx; dirZ = dz; }
  }
  // Drop waypoints that coincide with the current position.
  while (m.path.length > 0 && Math.hypot(m.path[0].x - m.x, m.path[0].z - m.z) < 1e-9) m.path.shift();
  if (dirX !== 0 || dirZ !== 0) m.heading = turnToward(m.heading, Math.atan2(dirX, dirZ), TURN_RATE * dt);
  return { moved, arrived: m.path.length === 0 };
}
