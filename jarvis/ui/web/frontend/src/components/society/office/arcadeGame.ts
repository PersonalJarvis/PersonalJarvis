/**
 * The break-room arcade game: "Asteroid Run", in 3D. The rocket flies into
 * the screen (-z) through an endless asteroid field; it steers in its own
 * plane (x, y) while low-poly rocks come at it out of the depth. Dodge them
 * or shoot them apart. It starts gentle and gets harder the longer you last:
 * the field comes faster, denser and with bigger rocks.
 *
 * Pure state and a fixed-rate `stepArcade`, so the rules are testable without
 * WebGL; the cabinet overlay (ArcadeCabinet) feeds input in and draws.
 */

/** Half-extent of the plane the rocket can steer in (world units). */
export const PLAY_X = 9;
export const PLAY_Y = 5;
/** Rocks appear this far ahead and are gone once they pass the camera. */
export const SPAWN_Z = -150;
export const DESPAWN_Z = 14;

export const SHIP_RADIUS = 0.6;
const SHIP_ACCEL = 70;
const SHIP_MAX_SPEED = 13;
/** Fraction of steering velocity kept per second when no key is held. */
const SHIP_DRAG = 0.01;
const LASER_SPEED = 150;
const LASER_COOLDOWN = 0.2;
export const RAPID_COOLDOWN = 0.08;
/** Twin cannons either side of the nose, fired as a pair. */
export const GUN_OFFSET = 0.55;
export const MAX_MISSILES = 3;
/** Seconds to reload one missile. */
const MISSILE_RELOAD = 3.5;
const MISSILE_SPEED = 55;
const MISSILE_MAX_SPEED = 150;
const MISSILE_ACCEL = 160;
/** Turn rate: how much of the way to the target heading is taken per second. */
const MISSILE_TURN = 7;
export const RAPID_S = 8;
const INVULNERABLE_S = 1.6;
export const MAX_SHIELDS = 5;
/** Forward speed of the field at the start, in units per second. */
const BASE_SPEED = 32;
const BOOST_FACTOR = 1.7;
const BOOST_DRAIN = 0.4;
const BOOST_RECHARGE = 0.14;
/** Number of rock silhouettes; the renderer builds one mesh per variant. */
export const ROCK_VARIANTS = 4;

export type ArcadePhase = "ready" | "playing" | "paused" | "over";
export type RockSize = 0 | 1 | 2;
export type PickupKind = "shield" | "rapid";

/** Radius, hit points and score per rock size (small, medium, large). */
export const ROCK_RADIUS: Record<RockSize, number> = { 0: 0.8, 1: 1.5, 2: 2.6 };
const ROCK_HP: Record<RockSize, number> = { 0: 1, 1: 2, 2: 4 };
const ROCK_POINTS: Record<RockSize, number> = { 0: 60, 1: 35, 2: 20 };

export interface Rock {
  /** Stable id, so a missile can keep chasing its rock. */
  id: number;
  x: number; y: number; z: number; vx: number; vy: number; vz: number;
  size: RockSize; hp: number; variant: number;
  /** Tumble: current rotation and spin per axis. */
  rx: number; ry: number; sx: number; sy: number;
  /** Shade 0..1 picked at birth, so the field is not one flat colour. */
  tone: number;
  /** Seconds of white flash after a hit that did not break it. */
  flash: number;
}
export interface Laser { x: number; y: number; z: number }
export interface Missile {
  x: number; y: number; z: number; vx: number; vy: number; vz: number;
  /** The rock it homes on; null once that rock is gone (it then flies on and burns out). */
  targetId: number | null;
  life: number;
}
export interface Pickup { x: number; y: number; z: number; kind: PickupKind; spin: number }
/** A fireball flash where something blew up; the renderer grows and fades it. */
export interface Blast { x: number; y: number; z: number; age: number; size: number }
export const BLAST_S = 0.45;
export interface Debris { x: number; y: number; z: number; vx: number; vy: number; vz: number; life: number; max: number; hot: boolean; spin: number }

export interface ArcadeState {
  phase: ArcadePhase;
  /** Seconds survived in this run; drives the difficulty. */
  time: number;
  score: number;
  shields: number;
  shipX: number; shipY: number; vx: number; vy: number;
  invulnerable: number;
  rapid: number;
  cooldown: number;
  /** Boost energy 0..1; `boosting` is whether boost was on in the last step. */
  boost: number;
  boosting: boolean;
  /** Forward speed of the field in the last step (the renderer streaks stars with it). */
  speed: number;
  lasers: Laser[];
  missiles: Missile[];
  /** Missiles ready to launch, and seconds until the next one is reloaded. */
  missileAmmo: number;
  missileReload: number;
  /** Seconds since the last laser volley, for the muzzle flash. */
  sinceShot: number;
  nextRockId: number;
  rocks: Rock[];
  pickups: Pickup[];
  debris: Debris[];
  blasts: Blast[];
  spawnTimer: number;
  /** Camera shake left, in seconds. */
  shake: number;
  /** Seconds since the last hit on the ship, for the shield flash. */
  sinceHit: number;
}

export interface ArcadeInput { left: boolean; right: boolean; up: boolean; down: boolean; fire: boolean; boost: boolean }

export const IDLE_INPUT: ArcadeInput = { left: false, right: false, up: false, down: false, fire: false, boost: false };

/** 1, 2, 3 … one level per 20 seconds survived. */
export function arcadeLevel(state: ArcadeState): number {
  return 1 + Math.floor(state.time / 20);
}

/** Seconds between new rocks: 0.9 s at the start, down towards 0.12 s. */
export function spawnInterval(time: number): number {
  return Math.max(0.12, 0.9 / (1 + time / 20));
}

/** How fast the field comes at you relative to the start: doubles after a minute. */
export function speedFactor(time: number): number {
  return 1 + time / 60;
}

export function newArcade(): ArcadeState {
  return {
    phase: "ready", time: 0, score: 0, shields: 3,
    shipX: 0, shipY: -1, vx: 0, vy: 0,
    invulnerable: 0, rapid: 0, cooldown: 0, boost: 1, boosting: false, speed: BASE_SPEED,
    lasers: [], missiles: [], missileAmmo: MAX_MISSILES, missileReload: 0, sinceShot: 99, nextRockId: 1, rocks: [], pickups: [], debris: [], blasts: [], spawnTimer: 0.6, shake: 0, sinceHit: 99,
  };
}

function makeRock(state: ArcadeState, size: RockSize, x: number, y: number, z: number, vx: number, vy: number, vz: number, random: () => number): Rock {
  return {
    id: state.nextRockId++, x, y, z, vx, vy, vz, size, hp: ROCK_HP[size], variant: Math.floor(random() * ROCK_VARIANTS),
    rx: random() * Math.PI * 2, ry: random() * Math.PI * 2, sx: (random() - 0.5) * 2, sy: (random() - 0.5) * 2,
    tone: random(), flash: 0,
  };
}

function spawnRock(state: ArcadeState, random: () => number): void {
  // Bigger rocks become more common as the run goes on.
  const bigChance = Math.min(0.4, 0.12 + state.time / 300);
  const roll = random();
  const size: RockSize = roll < bigChance ? 2 : roll < bigChance + 0.38 ? 1 : 0;
  // Most rocks cross the plane you fly in; some pass wide, for depth.
  const aimed = random() < 0.2 + Math.min(0.35, state.time / 200);
  const x = aimed ? state.shipX + (random() - 0.5) * 4 : (random() - 0.5) * (PLAY_X * 2 + 10);
  const y = aimed ? state.shipY + (random() - 0.5) * 3 : (random() - 0.5) * (PLAY_Y * 2 + 6);
  const drift = 1.5 + state.time / 40;
  state.rocks.push(makeRock(state, size, x, y, SPAWN_Z - random() * 20,
    (random() - 0.5) * drift, (random() - 0.5) * drift, random() * 10 * speedFactor(state.time), random));
}

function burst(state: ArcadeState, x: number, y: number, z: number, count: number, speed: number, hot: boolean, random: () => number): void {
  for (let i = 0; i < count; i += 1) {
    const u = random() * 2 - 1, a = random() * Math.PI * 2, s = Math.sqrt(1 - u * u);
    const v = speed * (0.35 + random() * 0.65), life = 0.5 + random() * 0.7;
    state.debris.push({ x, y, z, vx: s * Math.cos(a) * v, vy: s * Math.sin(a) * v, vz: u * v, life, max: life, hot, spin: (random() - 0.5) * 12 });
  }
  // A cap keeps a long, busy run from growing the debris list without bound.
  if (state.debris.length > 500) state.debris.splice(0, state.debris.length - 500);
}

/** Break a rock: lasers split it into smaller ones, a missile (`split` false) leaves nothing. */
function breakRock(state: ArcadeState, rock: Rock, random: () => number, split = true): void {
  state.score += ROCK_POINTS[rock.size];
  state.blasts.push({ x: rock.x, y: rock.y, z: rock.z, age: 0, size: ROCK_RADIUS[rock.size] * (split ? 1.6 : 2.4) });
  burst(state, rock.x, rock.y, rock.z, 10 + rock.size * 10, 6 + rock.size * 3, false, random);
  burst(state, rock.x, rock.y, rock.z, 6 + rock.size * 4, 9, true, random);
  if (split && rock.size > 0) {
    const child = (rock.size - 1) as RockSize;
    for (const side of [-1, 1]) {
      state.rocks.push(makeRock(state, child, rock.x + side * ROCK_RADIUS[child] * 0.7, rock.y, rock.z,
        rock.vx + side * (3 + random() * 3), rock.vy + (random() - 0.5) * 5, rock.vz, random));
    }
  }
  // Now and then a rock leaves something useful behind.
  const roll = random();
  if (roll < 0.07) state.pickups.push({ x: rock.x, y: rock.y, z: rock.z, kind: "shield", spin: 0 });
  else if (roll < 0.15) state.pickups.push({ x: rock.x, y: rock.y, z: rock.z, kind: "rapid", spin: 0 });
}

/**
 * Launch a guided missile at a rock (the player clicked it). Returns false when
 * no missile is loaded, the game is not running, or the rock is already gone.
 */
export function launchMissile(state: ArcadeState, rockId: number): boolean {
  if (state.phase !== "playing" || state.missileAmmo < 1) return false;
  if (!state.rocks.some((rock) => rock.id === rockId)) return false;
  state.missileAmmo -= 1;
  if (state.missileReload <= 0) state.missileReload = MISSILE_RELOAD;
  // Out of the belly, nudged up and forward, then it finds its way.
  state.missiles.push({ x: state.shipX, y: state.shipY - 0.3, z: -0.8, vx: state.vx * 0.3, vy: 6, vz: -MISSILE_SPEED, targetId: rockId, life: 6 });
  return true;
}

/** Ids of the rocks a missile is currently chasing (the renderer marks them). */
export function lockedRockIds(state: ArcadeState): Set<number> {
  const ids = new Set<number>();
  for (const m of state.missiles) if (m.targetId !== null) ids.add(m.targetId);
  return ids;
}

function hitShip(state: ArcadeState, random: () => number): void {
  state.shake = 0.45;
  state.sinceHit = 0;
  state.shields -= 1;
  burst(state, state.shipX, state.shipY, 0, 24, 8, true, random);
  state.blasts.push({ x: state.shipX, y: state.shipY, z: 0, age: 0, size: 2.2 });
  if (state.shields <= 0) {
    state.shields = 0;
    state.phase = "over";
    burst(state, state.shipX, state.shipY, 0, 60, 12, true, random);
  } else {
    state.invulnerable = INVULNERABLE_S;
    state.rapid = 0;
  }
}

/** Advance the game by `dt` seconds. `random` returns [0, 1) and is injectable for tests. */
export function stepArcade(state: ArcadeState, input: ArcadeInput, dt: number, random: () => number = Math.random): void {
  // Debris keeps flying on the game-over screen; everything else stops.
  for (const d of state.debris) { d.x += d.vx * dt; d.y += d.vy * dt; d.z += d.vz * dt; d.life -= dt; }
  state.debris = state.debris.filter((d) => d.life > 0);
  for (const b of state.blasts) { b.age += dt; b.z += (state.phase === "playing" ? state.speed * 0.5 : 0) * dt; }
  state.blasts = state.blasts.filter((b) => b.age < BLAST_S);
  state.shake = Math.max(0, state.shake - dt);
  state.sinceHit += dt;
  if (state.phase !== "playing") return;

  state.time += dt;
  state.invulnerable = Math.max(0, state.invulnerable - dt);
  state.rapid = Math.max(0, state.rapid - dt);
  state.cooldown = Math.max(0, state.cooldown - dt);

  // Boost: faster field, double score; it drains while held and refills slowly.
  const canBoost = state.boosting ? state.boost > 0 : state.boost > 0.2;
  state.boosting = input.boost && canBoost;
  state.boost = state.boosting ? Math.max(0, state.boost - BOOST_DRAIN * dt) : Math.min(1, state.boost + BOOST_RECHARGE * dt);
  state.speed = BASE_SPEED * speedFactor(state.time) * (state.boosting ? BOOST_FACTOR : 1);
  state.score += dt * state.speed * 0.25 * (state.boosting ? 2 : 1);

  // Steering: accelerate towards the held direction, drift to a stop otherwise.
  const ax = (input.right ? 1 : 0) - (input.left ? 1 : 0);
  const ay = (input.up ? 1 : 0) - (input.down ? 1 : 0);
  state.vx += ax * SHIP_ACCEL * dt;
  state.vy += ay * SHIP_ACCEL * dt;
  const drag = Math.pow(SHIP_DRAG, dt);
  if (ax === 0) state.vx *= drag;
  if (ay === 0) state.vy *= drag;
  const v = Math.hypot(state.vx, state.vy);
  if (v > SHIP_MAX_SPEED) { state.vx *= SHIP_MAX_SPEED / v; state.vy *= SHIP_MAX_SPEED / v; }
  state.shipX += state.vx * dt;
  state.shipY += state.vy * dt;
  if (Math.abs(state.shipX) > PLAY_X) { state.shipX = Math.sign(state.shipX) * PLAY_X; state.vx = 0; }
  if (Math.abs(state.shipY) > PLAY_Y) { state.shipY = Math.sign(state.shipY) * PLAY_Y; state.vy = 0; }

  // Lasers: twin guns while rapid fire lasts.
  state.sinceShot += dt;
  if (input.fire && state.cooldown <= 0) {
    state.lasers.push({ x: state.shipX - GUN_OFFSET, y: state.shipY, z: -1.2 }, { x: state.shipX + GUN_OFFSET, y: state.shipY, z: -1.2 });
    state.cooldown = state.rapid > 0 ? RAPID_COOLDOWN : LASER_COOLDOWN;
    state.sinceShot = 0;
  }

  // Missile reload, one at a time.
  if (state.missileAmmo < MAX_MISSILES) {
    state.missileReload -= dt;
    if (state.missileReload <= 0) {
      state.missileAmmo += 1;
      state.missileReload = state.missileAmmo < MAX_MISSILES ? MISSILE_RELOAD : 0;
    }
  }

  // New rocks.
  state.spawnTimer -= dt;
  while (state.spawnTimer <= 0) {
    spawnRock(state, random);
    state.spawnTimer += spawnInterval(state.time) * (0.6 + random() * 0.8);
  }

  // Everything ahead comes at the rocket with the field.
  for (const rock of state.rocks) {
    rock.x += rock.vx * dt; rock.y += rock.vy * dt; rock.z += (state.speed + rock.vz) * dt;
    rock.rx += rock.sx * dt; rock.ry += rock.sy * dt;
    rock.flash = Math.max(0, rock.flash - dt);
  }
  for (const p of state.pickups) { p.z += state.speed * 0.8 * dt; p.spin += dt * 3; }

  // Lasers against rocks: swept along z, so a fast shot never tunnels through.
  const broken = new Set<Rock>();
  state.lasers = state.lasers.filter((laser) => {
    const z0 = laser.z;
    laser.z -= LASER_SPEED * dt;
    if (laser.z < SPAWN_Z - 30) return false;
    for (const rock of state.rocks) {
      if (broken.has(rock)) continue;
      const r = ROCK_RADIUS[rock.size] + 0.35;
      const closing = (state.speed + rock.vz) * dt;
      if (rock.z > z0 + r || rock.z < laser.z - r - closing) continue;
      if ((laser.x - rock.x) ** 2 + (laser.y - rock.y) ** 2 > r * r) continue;
      rock.hp -= 1;
      rock.flash = 0.09;
      burst(state, laser.x, laser.y, rock.z + ROCK_RADIUS[rock.size] * 0.6, 4, 5, true, random);
      if (rock.hp <= 0) broken.add(rock);
      return false;
    }
    return true;
  });
  // Missiles home on their rock and blow it apart completely, whatever its size.
  const wrecked = new Set<Rock>();
  state.missiles = state.missiles.filter((m) => {
    m.life -= dt;
    const target = m.targetId === null ? undefined : state.rocks.find((rock) => rock.id === m.targetId && !broken.has(rock) && !wrecked.has(rock));
    if (!target) m.targetId = null;
    const speed = Math.min(MISSILE_MAX_SPEED, Math.hypot(m.vx, m.vy, m.vz) + MISSILE_ACCEL * dt);
    if (target) {
      // Lead the target a little, then turn the velocity towards it.
      const lead = Math.hypot(target.x - m.x, target.y - m.y, target.z - m.z) / speed;
      const tx = target.x + target.vx * lead - m.x, ty = target.y + target.vy * lead - m.y;
      const tz = target.z + (state.speed + target.vz) * lead - m.z;
      const tl = Math.hypot(tx, ty, tz) || 1;
      const k = Math.min(1, MISSILE_TURN * dt);
      m.vx += (tx / tl * speed - m.vx) * k; m.vy += (ty / tl * speed - m.vy) * k; m.vz += (tz / tl * speed - m.vz) * k;
    }
    const v = Math.hypot(m.vx, m.vy, m.vz) || 1;
    m.vx *= speed / v; m.vy *= speed / v; m.vz *= speed / v;
    m.x += m.vx * dt; m.y += m.vy * dt; m.z += m.vz * dt;
    // Exhaust: a hot spark and a puff of smoke behind it.
    if (random() < 0.8) {
      const life = 0.25 + random() * 0.3;
      state.debris.push({ x: m.x - m.vx / v * 0.6, y: m.y - m.vy / v * 0.6, z: m.z - m.vz / v * 0.6, vx: (random() - 0.5) * 2, vy: (random() - 0.5) * 2, vz: (random() - 0.5) * 2, life, max: life, hot: random() < 0.5, spin: 4 });
    }
    if (target && Math.hypot(target.x - m.x, target.y - m.y, target.z - m.z) < ROCK_RADIUS[target.size] + 0.6) {
      wrecked.add(target);
      return false;
    }
    return m.life > 0 && m.z > SPAWN_Z - 40 && m.z < DESPAWN_Z;
  });
  for (const rock of wrecked) burst(state, rock.x, rock.y, rock.z, 30 + rock.size * 15, 14, true, random);

  if (broken.size > 0 || wrecked.size > 0) {
    state.rocks = state.rocks.filter((rock) => !broken.has(rock) && !wrecked.has(rock));
    for (const rock of broken) breakRock(state, rock, random);
    for (const rock of wrecked) breakRock(state, rock, random, false);
  }

  // Pickups are collected by flying through them.
  state.pickups = state.pickups.filter((p) => {
    if (Math.abs(p.z) < 1.2 && Math.hypot(p.x - state.shipX, p.y - state.shipY) < 1.4) {
      if (p.kind === "shield") state.shields = Math.min(MAX_SHIELDS, state.shields + 1);
      else state.rapid = RAPID_S;
      state.score += 50;
      burst(state, p.x, p.y, p.z, 14, 6, true, random);
      return false;
    }
    return p.z < DESPAWN_Z;
  });

  // A rock reaching the rocket's plane (slightly forgiving hitbox).
  if (state.invulnerable <= 0) {
    const hit = state.rocks.find((rock) => {
      const r = ROCK_RADIUS[rock.size] * 0.8 + SHIP_RADIUS;
      return Math.abs(rock.z) < r && Math.hypot(rock.x - state.shipX, rock.y - state.shipY) < r;
    });
    if (hit) {
      state.rocks = state.rocks.filter((rock) => rock !== hit);
      burst(state, hit.x, hit.y, hit.z, 16, 7, false, random);
      hitShip(state, random);
    }
  }
  state.rocks = state.rocks.filter((rock) => rock.z < DESPAWN_Z);
}
