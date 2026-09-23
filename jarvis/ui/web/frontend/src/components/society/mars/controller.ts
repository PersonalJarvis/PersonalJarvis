import { BUILDING_COLLIDERS, PLAYER_SPAWN, WORLD_BOUNDS, surfaceHeight, type Collider, type Vec3 } from "./world";

export const FIXED_STEP = 1 / 120;
export const MAX_FRAME = 0.1;
export const PLAYER_RADIUS = 0.4;
export const PLAYER_HEIGHT = 1.8;
export const WALK_SPEED = 4.4;
export const RUN_SPEED = 7.5;
export const STEP_HEIGHT = 0.35;
export const MAX_SLOPE = Math.tan(38 * Math.PI / 180);
export interface PlayerInput { forward: number; right: number; run: boolean; jump: boolean }
export interface PlayerState { position: Vec3; yaw: number; velocityY: number; grounded: boolean; accumulator: number; jumpHeld: boolean }
export interface CollisionWorld { colliders: Collider[]; ground: (x: number, z: number) => number }
export const COLLISION_WORLD: CollisionWorld = { colliders: BUILDING_COLLIDERS, ground: surfaceHeight };

export function createPlayer(position: Vec3 = PLAYER_SPAWN): PlayerState {
  return { position: [...position], yaw: 0, velocityY: 0, grounded: true, accumulator: 0, jumpHeld: false };
}

export function isPositionClear(position: Vec3, world = COLLISION_WORLD): boolean {
  return !world.colliders.some((c) => position[1] + PLAYER_HEIGHT > c.min[1] && position[1] < c.max[1]
    && position[0] > c.min[0] - PLAYER_RADIUS && position[0] < c.max[0] + PLAYER_RADIUS
    && position[2] > c.min[2] - PLAYER_RADIUS && position[2] < c.max[2] + PLAYER_RADIUS);
}

/** Swept expanded AABBs prevent high speed tunneling; remaining motion slides along the wall. */
export function sweepSlide(position: Vec3, dx: number, dz: number, world = COLLISION_WORLD): Vec3 {
  const result: Vec3 = [...position];
  for (let iteration = 0; iteration < 3 && Math.abs(dx) + Math.abs(dz) > 1e-8; iteration++) {
    let first = 1, normalX = 0, normalZ = 0;
    for (const c of world.colliders) {
      if (position[1] + PLAYER_HEIGHT <= c.min[1] || position[1] >= c.max[1]) continue;
      let enter = -Infinity, leave = Infinity, nx = 0, nz = 0;
      for (const axis of [0, 2] as const) {
        const delta = axis === 0 ? dx : dz, start = result[axis];
        const min = c.min[axis] - PLAYER_RADIUS, max = c.max[axis] + PLAYER_RADIUS;
        if (Math.abs(delta) < 1e-12) {
          if (start <= min || start >= max) { leave = -Infinity; break; }
          continue;
        }
        let near = (min - start) / delta, far = (max - start) / delta;
        const normal = -Math.sign(delta);
        if (near > far) [near, far] = [far, near];
        if (near > enter) { enter = near; nx = axis === 0 ? normal : 0; nz = axis === 2 ? normal : 0; }
        leave = Math.min(leave, far);
      }
      if (enter >= -1e-7 && enter < first && enter <= leave && leave >= 0) {
        first = Math.max(0, enter); normalX = nx; normalZ = nz;
      }
    }
    const travel = Math.max(0, first - 0.00001);
    result[0] += dx * travel; result[2] += dz * travel;
    if (first === 1) break;
    dx *= 1 - first; dz *= 1 - first;
    const into = dx * normalX + dz * normalZ;
    dx -= normalX * into; dz -= normalZ * into;
  }
  return result;
}

function stepPlayer(state: PlayerState, input: PlayerInput, cameraYaw: number, dt: number, world: CollisionWorld) {
  const magnitude = Math.hypot(input.forward, input.right);
  if (magnitude > 0) {
    const speed = input.run ? RUN_SPEED : WALK_SPEED;
    const forward = input.forward / magnitude, right = input.right / magnitude;
    const dx = (right * Math.cos(cameraYaw) - forward * Math.sin(cameraYaw)) * speed * dt;
    const dz = (-right * Math.sin(cameraYaw) - forward * Math.cos(cameraYaw)) * speed * dt;
    const next = sweepSlide(state.position, dx, dz, world);
    const currentGround = world.ground(state.position[0], state.position[2]);
    const nextGround = world.ground(next[0], next[2]);
    const distance = Math.hypot(next[0] - state.position[0], next[2] - state.position[2]);
    // Preserve an explicit ledge boundary. Jumping does not grant wall/steep-slope traversal.
    const slope = (nextGround - currentGround) / Math.max(distance, 0.00001);
    const probeDistance = 0.5;
    const probe = world.ground(next[0] + dx / Math.max(Math.hypot(dx, dz), 0.00001) * probeDistance, next[2] + dz / Math.max(Math.hypot(dx, dz), 0.00001) * probeDistance);
    const smallStep = nextGround - currentGround <= STEP_HEIGHT && (probe - nextGround) / probeDistance <= MAX_SLOPE;
    const allowedRise = nextGround - state.position[1] <= STEP_HEIGHT;
    const safeEdge = currentGround - nextGround <= STEP_HEIGHT;
    if ((slope <= MAX_SLOPE || smallStep) && allowedRise && safeEdge) {
      state.position[0] = Math.max(WORLD_BOUNDS.min[0] + PLAYER_RADIUS, Math.min(WORLD_BOUNDS.max[0] - PLAYER_RADIUS, next[0]));
      state.position[2] = Math.max(WORLD_BOUNDS.min[2] + PLAYER_RADIUS, Math.min(WORLD_BOUNDS.max[2] - PLAYER_RADIUS, next[2]));
      state.yaw = Math.atan2(dx, dz);
    }
  }
  if (input.jump && !state.jumpHeld && state.grounded) { state.velocityY = 5.2; state.grounded = false; }
  state.jumpHeld = input.jump;
  const ground = world.ground(state.position[0], state.position[2]);
  state.velocityY -= 16 * dt;
  let y = state.position[1] + state.velocityY * dt;
  for (const c of world.colliders) {
    if (state.position[0] < c.min[0] - PLAYER_RADIUS || state.position[0] > c.max[0] + PLAYER_RADIUS
      || state.position[2] < c.min[2] - PLAYER_RADIUS || state.position[2] > c.max[2] + PLAYER_RADIUS) continue;
    if (state.velocityY > 0 && state.position[1] + PLAYER_HEIGHT <= c.min[1] && y + PLAYER_HEIGHT >= c.min[1]) {
      y = c.min[1] - PLAYER_HEIGHT - 0.001; state.velocityY = 0;
    }
    if (state.velocityY < 0 && state.position[1] >= c.max[1] && y <= c.max[1]) {
      y = c.max[1]; state.velocityY = 0; state.grounded = true;
    }
  }
  if (y <= ground + 0.001) { y = ground; state.velocityY = 0; state.grounded = true; }
  else if (state.velocityY !== 0) state.grounded = false;
  state.position[1] = y;
}

/** Drop excess suspended time instead of replaying seconds of movement after a hidden tab. */
export function advancePlayer(state: PlayerState, input: PlayerInput, cameraYaw: number, elapsed: number, world = COLLISION_WORLD): number {
  state.accumulator += Math.max(0, Math.min(MAX_FRAME, Number.isFinite(elapsed) ? elapsed : 0));
  let steps = 0;
  while (state.accumulator + 1e-10 >= FIXED_STEP && steps < 12) {
    stepPlayer(state, input, cameraYaw, FIXED_STEP, world);
    state.accumulator = Math.max(0, state.accumulator - FIXED_STEP);
    steps++;
  }
  return steps;
}
