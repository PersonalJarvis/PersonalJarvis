/** Metre-scale companion motion. Presentation has no task or audio authority. */
export type Point = [number, number, number];
export interface PlayerPose { position: Point; yaw: number }
export interface Obstacle { min: Point; max: Point }
export interface CompanionWorld {
  colliders: readonly Obstacle[];
  getGround: (x: number, z: number) => number;
  /** False for unloaded chunks, forbidden volumes or reserved interaction space. */
  isLoaded?: (position: Point) => boolean;
}
export interface Motion {
  position: Point;
  yaw: number;
  blocked: boolean;
  initialized: boolean;
  speed: number;
}
export const BODY_HEIGHT = 0.4;
export const CLEARANCE = 0.23;
export const MAX_SPEED = 9;
const EPSILON = 0.0001;

export function createMotion(): Motion {
  return { position: [0, 0, 0], yaw: 0, blocked: false, initialized: false, speed: 0 };
}

/** Stable side of the player, independent of camera yaw. Position is body bottom. */
export function followAnchor(player: PlayerPose, out: Point): Point {
  out[0] = player.position[0] + Math.cos(player.yaw) * 0.95 + Math.sin(player.yaw) * 0.35;
  out[1] = player.position[1] + 1.15;
  out[2] = player.position[2] - Math.sin(player.yaw) * 0.95 + Math.cos(player.yaw) * 0.35;
  return out;
}

export function positionClear(p: Point, world: CompanionWorld): boolean {
  if (!p.every(Number.isFinite) || world.isLoaded?.(p) === false) return false;
  const floor = world.getGround(p[0], p[2]);
  if (!Number.isFinite(floor) || p[1] < floor + 0.03) return false;
  return !world.colliders.some((box) => p[0] > box.min[0] - CLEARANCE
    && p[0] < box.max[0] + CLEARANCE && p[2] > box.min[2] - CLEARANCE
    && p[2] < box.max[2] + CLEARANCE && p[1] > box.min[1] - BODY_HEIGHT - 0.025
    && p[1] < box.max[1] + 0.025);
}

/** Earliest contact with expanded closed geometry, including thin walls/ceilings. */
function clearFraction(start: Point, end: Point, world: CompanionWorld): number {
  let first = 1;
  for (const box of world.colliders) {
    let enter = -Infinity, leave = Infinity;
    for (let axis = 0; axis < 3; axis++) {
      const min = box.min[axis] - (axis === 1 ? BODY_HEIGHT + 0.025 : CLEARANCE);
      const max = box.max[axis] + (axis === 1 ? 0.025 : CLEARANCE);
      const delta = end[axis] - start[axis];
      if (Math.abs(delta) < 1e-12) {
        if (start[axis] <= min || start[axis] >= max) { leave = -Infinity; break; }
      } else {
        const a = (min - start[axis]) / delta, b = (max - start[axis]) / delta;
        enter = Math.max(enter, Math.min(a, b));
        leave = Math.min(leave, Math.max(a, b));
      }
    }
    if (enter >= -EPSILON && leave >= Math.max(0, enter)) first = Math.min(first, Math.max(0, enter));
  }
  return first === 1 ? 1 : Math.max(0, first - EPSILON);
}

export function routeClear(start: Point, end: Point, world: CompanionWorld): boolean {
  if (clearFraction(start, end, world) < 1) return false;
  const count = Math.max(1, Math.ceil(Math.hypot(end[0] - start[0], end[1] - start[1], end[2] - start[2]) / 0.2));
  if (count > 200) return false;
  const sample: Point = [0, 0, 0];
  for (let i = 0; i <= count; i++) {
    for (let axis = 0; axis < 3; axis++) sample[axis] = start[axis] + (end[axis] - start[axis]) * i / count;
    if (!positionClear(sample, world)) return false;
  }
  return true;
}

/** Adjust hover height under a ceiling, without forcing the body into the floor. */
export function clearAnchor(anchor: Point, world: CompanionWorld): boolean {
  const original = anchor[1];
  for (let lower = 0; lower <= 1; lower += 0.1) {
    anchor[1] = original - lower;
    if (positionClear(anchor, world)) return true;
  }
  anchor[1] = original;
  return false;
}

/** Bounded local visibility graph. Replan at most twice per second, never per frame.
 * Returns no path when the world cannot prove a route; it never invents travel.
 */
export function planLocalRoute(start: Point, end: Point, world: CompanionWorld): Point[] {
  if (!positionClear(start, world) || !positionClear(end, world)) return [];
  if (routeClear(start, end, world)) return [[...end]];
  const nodes: Point[] = [[...start], [...end]];
  for (const box of world.colliders) {
    if (box.max[1] < Math.min(start[1], end[1]) || box.min[1] > Math.max(start[1], end[1]) + BODY_HEIGHT) continue;
    if (box.max[0] < Math.min(start[0], end[0]) - 8 || box.min[0] > Math.max(start[0], end[0]) + 8
      || box.max[2] < Math.min(start[2], end[2]) - 8 || box.min[2] > Math.max(start[2], end[2]) + 8) continue;
    for (const x of [box.min[0] - CLEARANCE - 0.08, box.max[0] + CLEARANCE + 0.08]) {
      for (const z of [box.min[2] - CLEARANCE - 0.08, box.max[2] + CLEARANCE + 0.08]) {
        const candidate: Point = [x, Math.min(start[1], end[1]), z];
        if (nodes.length < 50 && positionClear(candidate, world)) nodes.push(candidate);
      }
    }
  }
  const costs = nodes.map(() => Infinity), previous = nodes.map(() => -1);
  const visited = new Set<number>();
  costs[0] = 0;
  while (visited.size < nodes.length) {
    let current = -1;
    for (let i = 0; i < nodes.length; i++) if (!visited.has(i) && Number.isFinite(costs[i]) && (current < 0 || costs[i] < costs[current])) current = i;
    if (current < 0) return [];
    if (current === 1) {
      const result: Point[] = [];
      for (let i = 1; i !== 0; i = previous[i]) result.unshift(nodes[i]);
      return result;
    }
    visited.add(current);
    for (let next = 0; next < nodes.length; next++) {
      if (visited.has(next)) continue;
      const a = nodes[current], b = nodes[next];
      const cost = costs[current] + Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
      if (cost < costs[next] && routeClear(a, b, world)) { costs[next] = cost; previous[next] = current; }
    }
  }
  return [];
}

/** Bounded fixed substeps; background-tab time never becomes a teleport. */
export function advanceMotion(state: Motion, target: Point, world: CompanionWorld, delta: number, scratch: Point): void {
  if (!Number.isFinite(delta) || delta <= 0 || !target.every(Number.isFinite)) return;
  if (!state.initialized) {
    if (!positionClear(target, world)) { state.blocked = true; return; }
    state.position[0] = target[0]; state.position[1] = target[1]; state.position[2] = target[2];
    state.initialized = true;
  }
  state.blocked = false;
  let remaining = Math.min(delta, 0.1);
  while (remaining > 1e-8) {
    const dt = Math.min(remaining, 1 / 60);
    remaining -= dt;
    const p = state.position;
    const dx = target[0] - p[0], dy = target[1] - p[1], dz = target[2] - p[2];
    const distance = Math.hypot(dx, dy, dz);
    const fraction = distance < 1e-8 ? 0 : Math.min(1 - Math.exp(-5 * dt), MAX_SPEED * dt / distance);
    scratch[0] = p[0] + dx * fraction; scratch[1] = p[1] + dy * fraction; scratch[2] = p[2] + dz * fraction;
    const travel = clearFraction(p, scratch, world);
    for (let axis = 0; axis < 3; axis++) scratch[axis] = p[axis] + (scratch[axis] - p[axis]) * travel;
    if (!positionClear(scratch, world)) { state.blocked = true; state.speed = 0; break; }
    state.speed = Math.hypot(scratch[0] - p[0], scratch[1] - p[1], scratch[2] - p[2]) / dt;
    p[0] = scratch[0]; p[1] = scratch[1]; p[2] = scratch[2];
    if (travel < 1) { state.blocked = true; break; }
  }
}

/** Explicit recovery only; host must provide a validated loaded anchor. */
export function recoverAt(state: Motion, anchor: Point, world: CompanionWorld): boolean {
  if (!positionClear(anchor, world)) return false;
  state.position[0] = anchor[0]; state.position[1] = anchor[1]; state.position[2] = anchor[2];
  state.initialized = true; state.blocked = false; state.speed = 0;
  return true;
}

export type Expression = "idle" | "listening" | "speaking" | "working" | "approval" | "blocked" | "error" | "complete";
export interface AssistantPresentation {
  audio: "idle" | "listening" | "speaking";
  task: "idle" | "working" | "approval" | "blocked" | "error" | "complete";
  muted: boolean;
}
export function expressionFor(state: AssistantPresentation): Expression {
  if (state.task === "error" || state.task === "blocked" || state.task === "approval") return state.task;
  if (!state.muted && state.audio !== "idle") return state.audio;
  return state.task;
}
