/** Follow recorded footsteps, never the straight line to a moving owner. */
export type TrailPoint = [number, number, number];
export interface PetTrail { position: TrailPoint; points: TrailPoint[]; owner: TrailPoint; yaw: number; speed: number }
const distance = (a: TrailPoint, b: TrailPoint) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

export function createPetTrail(owner: TrailPoint): PetTrail {
  return { position: [...owner], owner: [...owner], points: [], yaw: 0, speed: 0 };
}

/** A small shoulder offset keeps the pet visible; never leave the verified
 * route unless the host can prove ground and clearance for the whole body. */
export function petDisplayPosition(position: TrailPoint, yaw: number, size: number, clear?: (point: TrailPoint, radius: number) => boolean): TrailPoint {
  if (!clear) return position;
  const offset = Math.min(0.38, size * 0.7);
  for (const side of [1, -1]) {
    const candidate: TrailPoint = [position[0] + Math.cos(yaw) * offset * side, position[1], position[2] - Math.sin(yaw) * offset * side];
    if (clear(candidate, size / 2)) return candidate;
  }
  return position;
}

export function recordOwner(trail: PetTrail, owner: TrailPoint, via?: TrailPoint): boolean {
  if (!owner.every(Number.isFinite)) return false;
  const gap = distance(trail.owner, owner);
  if (gap < 0.025) return true;
  // A refresh, teleport or missing stretch has no proven footsteps. Re-place
  // alongside the owner instead of drawing travel through unknown geometry.
  if (gap > 12 || trail.points.length > 512) {
    trail.position = [...owner]; trail.owner = [...owner]; trail.points = []; trail.speed = 0;
    return false;
  }
  if (via && via.every(Number.isFinite) && distance(trail.owner, via) > 0.025) trail.points.push([...via]);
  trail.points.push([...owner]); trail.owner = [...owner];
  return true;
}

export function advancePetTrail(trail: PetTrail, gap: number, delta: number): void {
  if (!Number.isFinite(delta) || delta <= 0) return;
  let length = 0, last = trail.position;
  for (const point of trail.points) { length += distance(last, point); last = point; }
  const dt = Math.min(delta, 0.1);
  let budget = Math.min(Math.max(0, length - gap), Math.min(8, 2 + Math.max(0, length - gap) * 2) * dt);
  const travel = budget;
  while (budget > 1e-7 && trail.points.length) {
    const next = trail.points[0];
    const segment = distance(trail.position, next);
    if (segment < 1e-7) { trail.points.shift(); continue; }
    const fraction = Math.min(1, budget / segment);
    const dx = next[0] - trail.position[0], dz = next[2] - trail.position[2];
    if (Math.hypot(dx, dz) > 1e-7) trail.yaw = Math.atan2(dx, dz);
    for (let axis = 0; axis < 3; axis++) trail.position[axis] += (next[axis] - trail.position[axis]) * fraction;
    budget -= segment * fraction;
    if (fraction === 1) trail.points.shift();
  }
  trail.speed = (travel - budget) / dt;
}
