/**
 * Flight of Gigi, the lead agent's mascot, over the office floor. The lead is
 * not drawn as a figure: Gigi hovers at chest/head height of the toy figures
 * and follows the walker's ground position (x, z). In "follow" mode it trails
 * the person's own character instead, hovering behind-beside its shoulder
 * (`followAnchor`): always on the coding floor, and on the agents floor
 * whenever no errand or summons takes it away.
 *
 * Pure and deterministic: the same state and inputs give the same pose. With
 * an `out` object a step allocates nothing, so it can run every frame.
 */

export type GigiFlightMode = "idle" | "work" | "talk" | "wave" | "sleep" | "follow";

export interface GigiFlightInput {
  /** The walker's ground position; Gigi hovers above it. */
  targetX: number;
  targetZ: number;
  /** True while the walker travels along a path. */
  moving: boolean;
  mode: GigiFlightMode;
  /** The assistant is talking right now (voice output). */
  speaking: boolean;
  /** Seconds on a monotonic clock; drives bob, drift and hops. */
  t: number;
  /** Seconds since the last step. Clamped internally to 0.1 s. */
  dt: number;
  /** Facing to settle on while hovering in place (rotation about +y, 0 = +z). */
  heading?: number;
  /** Reduced motion: static hover, no bob, drift, hops or spins. */
  reduced?: boolean;
  /** Is a point free airspace (not inside a wall or solid prop)? Gigi never settles where it is not. */
  clear?: (x: number, z: number) => boolean;
}

export interface GigiFlightPose {
  /** Body centre in world space. */
  x: number;
  y: number;
  z: number;
  /** Rotation about +y; 0 faces +z (the model's front). Includes happy spins. */
  yaw: number;
  /** Rotation about local x; positive tips the front down (leaning into travel). */
  pitch: number;
  /** Rotation about local z; banks into turns. */
  roll: number;
  /** Uniform scale multiplier around 1 (breathing, speaking pulse). */
  scale: number;
  /** Glow intensity, 0..1. */
  glow: number;
  /** Horizontal speed in m/s. */
  speed: number;
}

export interface GigiFlightState {
  x: number;
  z: number;
  vx: number;
  vz: number;
  /** Base hover height before bob/hops, spring-smoothed across modes. */
  base: number;
  vBase: number;
  /** Smoothed facing without the spin offset. */
  yaw: number;
  pitch: number;
  roll: number;
  /** Extra yaw of a running happy spin, in [0, 2π); 0 = no spin. */
  spin: number;
  /** 0..1 blends so mode changes glide instead of jumping. */
  idleBlend: number;
  workBlend: number;
  sleepBlend: number;
  happyBlend: number;
  glow: number;
  scale: number;
}

/** Chest/head height of the 1.3 m toy figures. */
export const GIGI_HOVER_M = 1.15;
export const GIGI_SLEEP_HOVER_M = 0.8;
export const GIGI_MIN_Y = 0.6;
export const GIGI_MAX_Y = 1.5;
/** Maximum forward lean (~15°). */
export const GIGI_MAX_LEAN = (15 * Math.PI) / 180;
/** Following the person: just above shoulder height of the 1.3 m figure. */
export const GIGI_FOLLOW_HOVER_M = 1.3;
/** The follow anchor sits this far behind and beside the person. */
export const GIGI_FOLLOW_BACK_M = 0.45;
export const GIGI_FOLLOW_SIDE_M = 0.6;
/** In follow mode Gigi trails the anchor by at most this much (a sprint stretches the spring). */
const FOLLOW_MAX_LAG_M = 1.1;
const MAX_BANK = 0.35;
/** Spring stiffness of horizontal follow; steady lag at walking pace is 2v/ω ≈ 0.4 m. */
const FOLLOW_OMEGA = 6.5;
const HEIGHT_OMEGA = 3.5;
/** Farther than this and the walker was placed, not walked: re-place Gigi too. */
const SNAP_DISTANCE_M = 4;
/** Gigi never trails the walker by more than this (keeps it off walls at corners). */
const MAX_LAG_M = 0.6;
const LEAN_FULL_SPEED = 1.6;
const SPIN_SECONDS = 0.9;
const SPIN_PERIOD = 4.5;
const TAU = Math.PI * 2;

function wrap(angle: number): number {
  let a = angle % TAU;
  if (a <= -Math.PI) a += TAU;
  else if (a > Math.PI) a -= TAU;
  return a;
}

function clamp(value: number, min: number, max: number): number {
  return value < min ? min : value > max ? max : value;
}

/** Frame-rate independent exponential approach factor. */
function approach(rate: number, dt: number): number {
  return 1 - Math.exp(-rate * dt);
}

export function createGigiFlight(x: number, z: number, heading = 0): GigiFlightState {
  return {
    x, z, vx: 0, vz: 0, base: GIGI_HOVER_M, vBase: 0, yaw: heading, pitch: 0, roll: 0, spin: 0,
    idleBlend: 1, workBlend: 0, sleepBlend: 0, happyBlend: 0, glow: 0.35, scale: 1,
  };
}

export function createGigiPose(): GigiFlightPose {
  return { x: 0, y: GIGI_HOVER_M, z: 0, yaw: 0, pitch: 0, roll: 0, scale: 1, glow: 0.35, speed: 0 };
}

/**
 * Exact critically damped spring step (no overshoot from rest, stable at any dt).
 * Writes the new position to `result[0]` and velocity to `result[1]`.
 */
function springStep(position: number, velocity: number, target: number, omega: number, dt: number, result: [number, number]): void {
  const offset = position - target;
  const decay = Math.exp(-omega * dt);
  const temp = (velocity + omega * offset) * dt;
  result[0] = target + (offset + temp) * decay;
  result[1] = (velocity - omega * temp) * decay;
}

const scratch: [number, number] = [0, 0];

export function stepGigiFlight(state: GigiFlightState, input: GigiFlightInput, out?: GigiFlightPose): GigiFlightPose {
  const pose = out ?? createGigiPose();
  const dt = Number.isFinite(input.dt) ? clamp(input.dt, 0, 0.1) : 0;
  const t = Number.isFinite(input.t) ? input.t : 0;
  const reduced = input.reduced === true;
  const { targetX, targetZ, mode } = input;
  const happy = mode === "talk" || mode === "wave" || input.speaking;
  const moving = input.moving;

  // ---- Horizontal follow ------------------------------------------------
  const px = state.x, pz = state.z;
  const gap = Math.hypot(targetX - state.x, targetZ - state.z);
  if (reduced || gap > SNAP_DISTANCE_M || !Number.isFinite(gap)) {
    state.x = Number.isFinite(targetX) ? targetX : state.x;
    state.z = Number.isFinite(targetZ) ? targetZ : state.z;
    state.vx = 0; state.vz = 0;
  } else if (dt > 0) {
    springStep(state.x, state.vx, targetX, FOLLOW_OMEGA, dt, scratch);
    state.x = scratch[0]; state.vx = scratch[1];
    springStep(state.z, state.vz, targetZ, FOLLOW_OMEGA, dt, scratch);
    state.z = scratch[0]; state.vz = scratch[1];
    const lag = Math.hypot(state.x - targetX, state.z - targetZ);
    const maxLag = mode === "follow" ? FOLLOW_MAX_LAG_M : MAX_LAG_M;
    if (lag > maxLag) {
      const k = maxLag / lag;
      state.x = targetX + (state.x - targetX) * k;
      state.z = targetZ + (state.z - targetZ) * k;
    }
  }
  // Never settle inside a wall: pull back along the lag towards the (clear) target.
  if (input.clear && !input.clear(state.x, state.z)) {
    for (const k of [0.5, 0.25, 0]) {
      const x = targetX + (state.x - targetX) * k, z = targetZ + (state.z - targetZ) * k;
      if (k === 0 || input.clear(x, z)) { state.x = x; state.z = z; break; }
    }
  }
  const speed = dt > 0 ? Math.hypot(state.x - px, state.z - pz) / dt : 0;

  // ---- Mode blends --------------------------------------------------------
  const blend = approach(3, dt);
  const hovering = mode === "idle" || mode === "follow";
  state.idleBlend += ((!moving && hovering && !input.speaking ? 1 : 0) - state.idleBlend) * blend;
  state.workBlend += ((mode === "work" && !input.speaking ? 1 : 0) - state.workBlend) * blend;
  state.sleepBlend += ((mode === "sleep" && !input.speaking ? 1 : 0) - state.sleepBlend) * blend;
  state.happyBlend += ((happy ? 1 : 0) - state.happyBlend) * blend;

  // ---- Height -------------------------------------------------------------
  const baseTarget = mode === "sleep" && !input.speaking ? GIGI_SLEEP_HOVER_M : mode === "follow" ? GIGI_FOLLOW_HOVER_M : GIGI_HOVER_M;
  if (reduced) { state.base = baseTarget; state.vBase = 0; }
  else if (dt > 0) {
    springStep(state.base, state.vBase, baseTarget, HEIGHT_OMEGA, dt, scratch);
    state.base = scratch[0]; state.vBase = scratch[1];
  }
  let y = state.base;
  let driftX = 0, driftZ = 0, wiggle = 0;
  if (!reduced) {
    const calm = 1 - state.sleepBlend;
    // Gentle hover bob, ~0.06 m at 0.5 Hz; quieter while busy.
    y += Math.sin(t * Math.PI) * 0.06 * calm * (1 - 0.6 * state.workBlend);
    // Busy: small quick bobs as if typing along.
    y += Math.sin(t * TAU * 2.4) * 0.018 * state.workBlend;
    // Sleep: slow breathing.
    y += Math.sin(t * TAU * 0.2) * 0.03 * state.sleepBlend;
    // Happy hops.
    y += Math.abs(Math.sin(t * Math.PI * 1.5)) * 0.07 * state.happyBlend;
    // Idle: a slow figure-8 drift around the anchor.
    const w = t * 0.45;
    driftX = Math.sin(w) * 0.1 * state.idleBlend;
    driftZ = Math.sin(2 * w) * 0.05 * state.idleBlend;
    wiggle = Math.sin(t * 3.2) * 0.22 * state.happyBlend * (mode === "wave" ? 0 : 1);
  }
  y = clamp(y, GIGI_MIN_Y, GIGI_MAX_Y);

  // ---- Facing, lean and bank ---------------------------------------------
  const previousYaw = state.yaw;
  if (moving && speed > 0.15 && !reduced) {
    const travel = Math.atan2(state.x - px, state.z - pz);
    state.yaw = wrap(state.yaw + wrap(travel - state.yaw) * approach(8, dt));
  } else if (input.heading !== undefined && Number.isFinite(input.heading)) {
    state.yaw = reduced ? wrap(input.heading) : wrap(state.yaw + wrap(input.heading - state.yaw) * approach(5, dt));
  }
  const yawRate = dt > 0 ? wrap(state.yaw - previousYaw) / dt : 0;
  const leanTarget = reduced ? 0 : GIGI_MAX_LEAN * clamp(speed / LEAN_FULL_SPEED, 0, 1);
  // Turning towards +x (yaw increasing) tips the top towards +x: negative roll.
  const bankTarget = reduced ? 0 : clamp(-yawRate * clamp(speed, 0, 2) * 0.12, -MAX_BANK, MAX_BANK);
  state.pitch += (leanTarget - state.pitch) * approach(6, dt);
  state.roll += (bankTarget - state.roll) * approach(6, dt);

  // ---- Happy spin (wave): one full turn every few seconds, always finished ----
  if (reduced) state.spin = 0;
  else if (state.spin > 0 || (mode === "wave" && !moving && t % SPIN_PERIOD < SPIN_SECONDS * 0.5)) {
    state.spin += (dt * TAU) / SPIN_SECONDS;
    if (state.spin >= TAU) state.spin = 0;
  }

  // ---- Glow and scale -----------------------------------------------------
  let glowTarget = 0.35 + 0.15 * state.workBlend - 0.2 * state.sleepBlend + 0.4 * state.happyBlend;
  let scaleTarget = 1;
  if (!reduced) {
    glowTarget += Math.sin(t * TAU * 2.4) * 0.06 * state.workBlend + Math.sin(t * TAU * 0.2) * 0.08 * state.sleepBlend;
    scaleTarget += Math.sin(t * TAU * 0.2) * 0.03 * state.sleepBlend;
    if (input.speaking) { glowTarget += 0.1 + Math.sin(t * 9) * 0.1; scaleTarget += 0.03 + Math.sin(t * 9) * 0.02; }
  } else if (input.speaking) glowTarget += 0.1;
  state.glow += (clamp(glowTarget, 0, 1) - state.glow) * (reduced ? 1 : approach(10, dt));
  state.scale += (scaleTarget - state.scale) * (reduced ? 1 : approach(10, dt));

  pose.x = state.x + driftX;
  pose.y = y;
  pose.z = state.z + driftZ;
  pose.yaw = wrap(state.yaw + state.spin + wiggle);
  pose.pitch = state.pitch;
  pose.roll = state.roll;
  pose.scale = state.scale;
  pose.glow = clamp(state.glow, 0, 1);
  pose.speed = speed;
  return pose;
}

export interface FollowAnchor { x: number; z: number; /** +1 or -1: which shoulder Gigi keeps to; carry it into the next call. */ side: 1 | -1 }

/**
 * Where Gigi hovers while following a walker at (x, z) facing `heading`
 * (0 = +z): behind and beside its shoulder, on the side it already keeps to
 * unless that side is blocked (then the other side, then straight behind,
 * then right above the walker). `clear` tests free airspace; a candidate
 * counts only when the midpoint to it is clear too, so no wall sits between.
 */
export function followAnchor(x: number, z: number, heading: number, side: 1 | -1, clear?: (x: number, z: number) => boolean): FollowAnchor {
  const fx = Math.sin(heading), fz = Math.cos(heading);
  // The walker's right hand (heading 0 faces +z, so its right is -x).
  const rx = -fz, rz = fx;
  const candidates: [number, number, 1 | -1][] = [
    [GIGI_FOLLOW_BACK_M, GIGI_FOLLOW_SIDE_M * side, side],
    [GIGI_FOLLOW_BACK_M, -GIGI_FOLLOW_SIDE_M * side, side === 1 ? -1 : 1],
    [GIGI_FOLLOW_BACK_M + 0.2, 0, side],
    [0.25, 0, side],
  ];
  for (const [back, lateral, keep] of candidates) {
    const ax = x - fx * back + rx * lateral, az = z - fz * back + rz * lateral;
    if (!clear || (clear(ax, az) && clear((x + ax) / 2, (z + az) / 2))) return { x: ax, z: az, side: keep };
  }
  return { x, z, side };
}
