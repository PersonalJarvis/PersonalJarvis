import { CAMERA_CLEARANCE, segmentBox } from "./camera";
import { BUILDING_COLLIDERS, cameraGroundHeight, WORLD_BOUNDS, type Collider, type Vec3 } from "./world";
import type { CameraPose } from "./viewPreferences";

type Ground = (x: number, z: number) => number;
const interpolate = (a: Vec3, b: Vec3, t: number): Vec3 => a.map((n, i) => n + (b[i] - n) * t) as Vec3;

export function orbitLensIsClear(position: Vec3, colliders: Collider[] = BUILDING_COLLIDERS, ground: Ground = cameraGroundHeight): boolean {
  return position.every(Number.isFinite)
    && position[1] >= ground(position[0], position[2]) + CAMERA_CLEARANCE
    && !colliders.some(box => position.every((n, i) => n >= box.min[i] - CAMERA_CLEARANCE && n <= box.max[i] + CAMERA_CLEARANCE));
}

/** Sweep the lens from its previous pose, never from the inspection pivot.
 * A subject's centre is often inside a building or below its floor. Treating
 * that sightline as a follow-camera tether collapses the first wheel gesture.
 */
export function constrainOrbitPose(previous: CameraPose, requested: CameraPose, colliders: Collider[] = BUILDING_COLLIDERS, ground: Ground = cameraGroundHeight): CameraPose {
  const target = requested.target.map((n, i) => Math.max(WORLD_BOUNDS.min[i], Math.min(WORLD_BOUNDS.max[i], n))) as Vec3;
  const position = requested.position.map((n, i) => n + (target[i] - requested.target[i])) as Vec3;
  const distance = Math.hypot(...position.map((n, i) => n - previous.position[i]));
  if (distance < 1e-8) return { position, target };
  let fraction = 1;
  for (const box of colliders) {
    const hit = segmentBox(previous.position, position, box, CAMERA_CLEARANCE);
    if (hit !== null) fraction = Math.min(fraction, Math.max(0, hit - 0.001 / distance));
  }
  // Terrain is a continuous height field plus the authored cliff footprint.
  // Refine the first crossing so large wheel deltas stop at the same surface.
  const samples = Math.min(1024, Math.max(8, Math.ceil(distance / 0.25)));
  let safe = 0;
  for (let i = 1; i <= samples; i++) {
    const t = Math.min(fraction, i / samples);
    const p = interpolate(previous.position, position, t);
    if (p[1] < ground(p[0], p[2]) + CAMERA_CLEARANCE) {
      let blocked = t;
      for (let step = 0; step < 16; step++) {
        const mid = (safe + blocked) / 2, q = interpolate(previous.position, position, mid);
        if (q[1] < ground(q[0], q[2]) + CAMERA_CLEARANCE) blocked = mid;
        else safe = mid;
      }
      fraction = safe;
      break;
    }
    safe = t;
    if (t >= fraction) break;
  }
  if (fraction === 1) return { position, target };
  return {
    position: interpolate(previous.position, position, fraction),
    target: interpolate(previous.target, target, fraction),
  };
}
