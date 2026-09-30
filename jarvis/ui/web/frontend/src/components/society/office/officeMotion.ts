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

/** Two bodies never come closer than this (centre to centre), in metres. */
export const BODY_SPACING = 0.62;

/**
 * Local avoidance: a sideways-biased push away from nearby bodies, strongest
 * when they touch. Zero when nobody is within `radius`. Pure.
 */
export function separation(self: Point, heading: number, others: Iterable<Point>, radius = BODY_SPACING * 1.4): Point {
  let x = 0, z = 0;
  const fx = Math.sin(heading), fz = Math.cos(heading);
  for (const o of others) {
    const dx = self.x - o.x, dz = self.z - o.z;
    const d = Math.hypot(dx, dz);
    if (d < 1e-6 || d >= radius) continue;
    const w = (radius - d) / radius;
    x += (dx / d) * w; z += (dz / d) * w;
    // Someone straight ahead: add a sidestep to the right so two walkers pass instead of pushing head-on.
    const ahead = -(dx * fx + dz * fz) / d;
    if (ahead > 0.6) { x += fz * w * 0.8; z += -fx * w * 0.8; }
  }
  return { x, z };
}

/** True when a point keeps the spacing from every other body. */
export function clearOfBodies(p: Point, others: Iterable<Point>, spacing = BODY_SPACING): boolean {
  for (const o of others) if (Math.hypot(p.x - o.x, p.z - o.z) < spacing) return false;
  return true;
}

/**
 * May a figure step from `from` to `next` among `others`? Yes when the step
 * keeps the spacing from everyone, or when every body it would touch there is
 * one it already touches and the step does not bring it closer. Only touching
 * bodies count: a far-away figure must never veto the way out of a crowd,
 * or someone pinned in a corner could not move at all.
 */
export function stepClearOfBodies(next: Point, from: Point, others: Iterable<Point>, spacing = BODY_SPACING): boolean {
  for (const o of others) {
    const after = Math.hypot(next.x - o.x, next.z - o.z);
    if (after >= spacing) continue;
    if (after < Math.hypot(from.x - o.x, from.z - o.z)) return false;
  }
  return true;
}

/**
 * Nudge a mover sideways out of others' way, only onto walkable ground.
 * Called after `stepMover`; the path pulls it back on the next frames.
 */
export function applySeparation(m: Mover, push: Point, dt: number, walkable: (p: Point) => boolean, maxSpeed = 0.9): void {
  const len = Math.hypot(push.x, push.z);
  if (len < 1e-6) return;
  const step = Math.min(len, 1) * maxSpeed * dt;
  const nx = m.x + (push.x / len) * step, nz = m.z + (push.z / len) * step;
  if (walkable({ x: nx, z: nz })) { m.x = nx; m.z = nz; }
}

/**
 * Walk the path while steering around other bodies: the heading blends the
 * next waypoint with a push away from anyone close, and the walker slows when
 * someone stands right ahead. Falls back to plain path following whenever the
 * steered step would leave walkable ground.
 */
export function stepMoverAvoiding(m: Mover, speed: number, dt: number, others: readonly Point[], walkable: (p: Point) => boolean): { moved: number; arrived: boolean } {
  if (m.path.length === 0) return { moved: 0, arrived: true };
  const next = m.path[0];
  const dx = next.x - m.x, dz = next.z - m.z;
  const dist = Math.hypot(dx, dz);
  const push = separation(m, m.heading, others, 1.2);
  const pushLen = Math.hypot(push.x, push.z);
  if (pushLen < 1e-3 || dist < 0.2) return stepMover(m, speed, dt);
  let ax = dx / dist + push.x * 1.6, az = dz / dist + push.z * 1.6;
  const len = Math.hypot(ax, az) || 1;
  ax /= len; az /= len;
  // Slow down while someone blocks the way ahead.
  let slow = 1;
  for (const o of others) {
    const ox = o.x - m.x, oz = o.z - m.z;
    const d = Math.hypot(ox, oz);
    if (d < 0.95 && d > 1e-6 && (ox * dx + oz * dz) / (d * dist) > 0.7) slow = Math.min(slow, Math.max(0.25, (d - BODY_SPACING) / 0.4));
  }
  const step = Math.min(speed * slow * dt, dist);
  const cand = { x: m.x + ax * step, z: m.z + az * step };
  if (!walkable(cand)) return stepMover(m, speed, dt);
  m.x = cand.x; m.z = cand.z;
  if (Math.hypot(next.x - m.x, next.z - m.z) < 0.12) m.path.shift();
  m.heading = turnToward(m.heading, Math.atan2(ax, az), TURN_RATE * dt);
  return { moved: step, arrived: m.path.length === 0 };
}
