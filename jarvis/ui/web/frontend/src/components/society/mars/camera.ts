import { Vector3 } from "three";
import { BUILDING_COLLIDERS, terrainHeight, type Bounds, type Collider, type Vec3 } from "./world";

export const CAMERA_FOV = 45;
export const MIN_POLAR = 0.035;
export const MAX_POLAR = Math.PI / 2 - 0.035;
export const CAMERA_CLEARANCE = 0.35;

export function boundsCorners(bounds: Bounds): Vec3[] {
  const corners: Vec3[] = [];
  for (const x of [bounds.min[0], bounds.max[0]]) for (const y of [bounds.min[1], bounds.max[1]]) for (const z of [bounds.min[2], bounds.max[2]]) corners.push([x, y, z]);
  return corners;
}

/** Solve every corner against horizontal AND vertical frusta, including its depth. */
export function fitWorldBounds(bounds: Bounds, aspect: number, padding = 1.15, direction: Vec3 = [0.8, 0.9, 1]) {
  const target = new Vector3().fromArray(bounds.min).add(new Vector3().fromArray(bounds.max)).multiplyScalar(0.5);
  const outward = new Vector3(...direction).normalize();
  const right = new Vector3().crossVectors(new Vector3(0, 1, 0), outward).normalize();
  const up = new Vector3().crossVectors(outward, right).normalize();
  const tanV = Math.tan(CAMERA_FOV * Math.PI / 360);
  const tanH = tanV * Math.max(0.05, aspect);
  let distance = 1;
  for (const corner of boundsCorners(bounds)) {
    const offset = new Vector3(...corner).sub(target);
    distance = Math.max(distance, offset.dot(outward) + Math.max(Math.abs(offset.dot(right)) * padding / tanH, Math.abs(offset.dot(up)) * padding / tanV) + 1);
  }
  return { target: target.toArray() as Vec3, position: target.clone().addScaledVector(outward, distance).toArray() as Vec3, distance };
}

/** A frame may look through parts of its subject; only its lens must be clear.
 * Moving farther along the same direction preserves the complete bounds fit.
 */
export function frameInspectionBounds(bounds: Bounds, aspect: number, padding = 1.15, direction: Vec3 = [0.8, 0.9, 1], colliders = BUILDING_COLLIDERS, ground = terrainHeight) {
  const frame = fitWorldBounds(bounds, aspect, padding, direction);
  const target = new Vector3(...frame.target);
  const outward = new Vector3(...direction).normalize();
  for (let attempt = 0; attempt < 32; attempt++) {
    const position = target.clone().addScaledVector(outward, frame.distance).toArray() as Vec3;
    const inSolid = colliders.some((box) => position.every((n, axis) => n >= box.min[axis] - CAMERA_CLEARANCE && n <= box.max[axis] + CAMERA_CLEARANCE));
    if (!inSolid && position[1] >= ground(position[0], position[2]) + CAMERA_CLEARANCE) return { ...frame, position };
    frame.distance *= 1.2;
  }
  throw new Error("No clear Mars inspection camera position");
}

export function segmentBox(from: Vec3, to: Vec3, bounds: Collider, radius = 0): number | null {
  let enter = 0, leave = 1;
  for (let axis = 0; axis < 3; axis++) {
    const delta = to[axis] - from[axis];
    const min = bounds.min[axis] - radius, max = bounds.max[axis] + radius;
    if (Math.abs(delta) < 1e-10) {
      if (from[axis] < min || from[axis] > max) return null;
      continue;
    }
    let near = (min - from[axis]) / delta, far = (max - from[axis]) / delta;
    if (near > far) [near, far] = [far, near];
    enter = Math.max(enter, near);
    leave = Math.min(leave, far);
    if (enter > leave) return null;
  }
  return enter;
}

/** Shorten the sightline against solid geometry and terrain; never send the camera through a roof. */
export function avoidCameraCollision(target: Vec3, desired: Vec3, colliders = BUILDING_COLLIDERS, ground = terrainHeight): Vec3 {
  const inside = (point: Vec3, c: Collider) => point.every((value, axis) => value >= c.min[axis] - CAMERA_CLEARANCE && value <= c.max[axis] + CAMERA_CLEARANCE);
  let endpoint: Vec3 = [...desired];
  // Inspecting an object may put the pivot inside it. Keep the lens outside
  // that object instead of collapsing the camera onto the occupied pivot.
  for (const collider of colliders) {
    if (!inside(target, collider) || !inside(endpoint, collider)) continue;
    const direction = new Vector3(...endpoint).sub(new Vector3(...target));
    if (direction.lengthSq() < 1e-10) direction.set(0, 0, 1);
    direction.normalize();
    let exitDistance = Infinity;
    for (let axis = 0; axis < 3; axis++) {
      const delta = direction.getComponent(axis);
      if (Math.abs(delta) < 1e-10) continue;
      const boundary = delta > 0 ? collider.max[axis] + CAMERA_CLEARANCE : collider.min[axis] - CAMERA_CLEARANCE;
      exitDistance = Math.min(exitDistance, (boundary - target[axis]) / delta);
    }
    endpoint = target.map((value, axis) => value + direction.getComponent(axis) * (exitDistance + 0.01)) as Vec3;
  }
  let fraction = 1;
  for (const collider of colliders) {
    if (inside(target, collider)) continue;
    const hit = segmentBox(target, endpoint, collider, CAMERA_CLEARANCE);
    if (hit !== null) fraction = Math.min(fraction, Math.max(0, hit - 0.002));
  }
  const length = Math.hypot(...endpoint.map((v, i) => v - target[i]));
  const count = Math.min(1024, Math.max(8, Math.ceil(length / 2)));
  for (let i = 1; i <= count; i++) {
    const t = i / count;
    if (t > fraction) break;
    const x = target[0] + (endpoint[0] - target[0]) * t, y = target[1] + (endpoint[1] - target[1]) * t, z = target[2] + (endpoint[2] - target[2]) * t;
    if (y < ground(x, z) + CAMERA_CLEARANCE) {
      fraction = (i - 1) / count;
      break;
    }
  }
  return target.map((value, axis) => value + (endpoint[axis] - value) * fraction) as Vec3;
}
