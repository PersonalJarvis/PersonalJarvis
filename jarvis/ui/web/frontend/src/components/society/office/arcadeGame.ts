/**
 * The break-room arcade game: "Asteroid Run". A rocket flies up through an
 * endless asteroid field; dodge or shoot the rocks. It starts gentle and gets
 * harder the longer you survive: rocks come faster, denser and bigger.
 *
 * Pure state and a fixed-rate `stepArcade`, so the rules are testable without
 * a canvas; the cabinet overlay (ArcadeCabinet) only feeds input and draws.
 */

/** Playfield size in logical units; the canvas scales it up. */
export const ARCADE_W = 240;
export const ARCADE_H = 320;

export const SHIP_RADIUS = 6;
const SHIP_ACCEL = 900;
const SHIP_MAX_SPEED = 150;
/** Fraction of velocity kept per second when no key is held (gentle drift). */
const SHIP_DRAG = 0.02;
const SHOT_SPEED = 330;
const SHOT_COOLDOWN = 0.2;
const RAPID_COOLDOWN = 0.08;
/** Seconds of blinking invulnerability after losing a life. */
const INVULNERABLE_S = 2;
export const RAPID_S = 8;
const MAX_LIVES = 5;

export type ArcadePhase = "ready" | "playing" | "paused" | "over";
export type RockSize = 0 | 1 | 2;
export type PickupKind = "shield" | "rapid" | "life";

/** Radius, hit points and score per rock size (small, medium, large). */
export const ROCK_RADIUS: Record<RockSize, number> = { 0: 6, 1: 11, 2: 17 };
const ROCK_HP: Record<RockSize, number> = { 0: 1, 1: 2, 2: 4 };
const ROCK_POINTS: Record<RockSize, number> = { 0: 100, 1: 50, 2: 25 };

export interface Rock {
  x: number; y: number; vx: number; vy: number; size: RockSize; hp: number;
  angle: number; spin: number;
  /** Per-vertex radius factors: the jagged outline. */
  shape: number[];
  /** Seconds of white flash after a hit that did not break it. */
  flash: number;
}
export interface Shot { x: number; y: number }
export interface Pickup { x: number; y: number; kind: PickupKind; t: number }
export interface Particle { x: number; y: number; vx: number; vy: number; life: number; max: number; colour: string }
export interface Star { x: number; y: number; depth: number }

export interface ArcadeState {
  phase: ArcadePhase;
  /** Seconds survived in this run; drives the difficulty. */
  time: number;
  score: number;
  lives: number;
  shipX: number; shipY: number; vx: number; vy: number;
  invulnerable: number;
  shield: boolean;
  rapid: number;
  cooldown: number;
  shots: Shot[];
  rocks: Rock[];
  pickups: Pickup[];
  particles: Particle[];
  stars: Star[];
  spawnTimer: number;
  /** Screen shake left, in seconds. */
  shake: number;
}

export interface ArcadeInput { left: boolean; right: boolean; up: boolean; down: boolean; fire: boolean }

/** 1, 2, 3 … one level per 20 seconds survived. */
export function arcadeLevel(state: ArcadeState): number {
  return 1 + Math.floor(state.time / 20);
}

/** Seconds between new rocks: 1.3 s at the start, down towards 0.2 s. */
export function spawnInterval(time: number): number {
  return Math.max(0.2, 1.3 / (1 + time / 25));
}

/** How fast rocks fall relative to the start: doubles after about 50 s. */
export function speedFactor(time: number): number {
  return 1 + time / 50;
}

export function newArcade(random: () => number = Math.random): ArcadeState {
  const stars: Star[] = [];
  for (let i = 0; i < 70; i += 1) stars.push({ x: random() * ARCADE_W, y: random() * ARCADE_H, depth: 0.2 + random() * 0.8 });
  return {
    phase: "ready", time: 0, score: 0, lives: 3,
    shipX: ARCADE_W / 2, shipY: ARCADE_H - 40, vx: 0, vy: 0,
    invulnerable: 0, shield: false, rapid: 0, cooldown: 0,
    shots: [], rocks: [], pickups: [], particles: [], stars, spawnTimer: 0.8, shake: 0,
  };
}

function makeRock(size: RockSize, x: number, y: number, vx: number, vy: number, random: () => number): Rock {
  const points = 9 + size * 2;
  const shape = Array.from({ length: points }, () => 0.72 + random() * 0.4);
  return { x, y, vx, vy, size, hp: ROCK_HP[size], angle: random() * Math.PI * 2, spin: (random() - 0.5) * 2.4, shape, flash: 0 };
}

function spawnRock(state: ArcadeState, random: () => number): void {
  // Bigger rocks become more common as the run goes on.
  const bigChance = Math.min(0.45, 0.12 + state.time / 250);
  const roll = random();
  const size: RockSize = roll < bigChance ? 2 : roll < bigChance + 0.4 ? 1 : 0;
  const r = ROCK_RADIUS[size];
  const speed = (40 + random() * 35) * speedFactor(state.time) * (size === 0 ? 1.25 : 1);
  const x = r + random() * (ARCADE_W - 2 * r);
  state.rocks.push(makeRock(size, x, -r - 2, (random() - 0.5) * 40, speed, random));
}

function burst(state: ArcadeState, x: number, y: number, count: number, colour: string, speed: number, random: () => number): void {
  for (let i = 0; i < count; i += 1) {
    const a = random() * Math.PI * 2, v = speed * (0.3 + random() * 0.7), life = 0.35 + random() * 0.5;
    state.particles.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, life, max: life, colour });
  }
}

function breakRock(state: ArcadeState, rock: Rock, random: () => number): void {
  state.score += ROCK_POINTS[rock.size];
  burst(state, rock.x, rock.y, 8 + rock.size * 8, "#c9b79c", 60 + rock.size * 20, random);
  burst(state, rock.x, rock.y, 5 + rock.size * 3, "#ffb454", 90, random);
  if (rock.size > 0) {
    const child = (rock.size - 1) as RockSize;
    for (const side of [-1, 1]) {
      state.rocks.push(makeRock(child, rock.x + side * ROCK_RADIUS[child] * 0.6, rock.y,
        rock.vx + side * (25 + random() * 25), rock.vy * (0.85 + random() * 0.3), random));
    }
  }
  // Now and then a rock leaves something useful behind.
  const roll = random();
  if (roll < 0.03) state.pickups.push({ x: rock.x, y: rock.y, kind: "life", t: 0 });
  else if (roll < 0.09) state.pickups.push({ x: rock.x, y: rock.y, kind: "shield", t: 0 });
  else if (roll < 0.15) state.pickups.push({ x: rock.x, y: rock.y, kind: "rapid", t: 0 });
}

function hitShip(state: ArcadeState, random: () => number): void {
  state.shake = 0.35;
  if (state.shield) {
    state.shield = false;
    state.invulnerable = 1;
    burst(state, state.shipX, state.shipY, 18, "#7dd3fc", 120, random);
    return;
  }
  state.lives -= 1;
  burst(state, state.shipX, state.shipY, 30, "#ff7a59", 140, random);
  if (state.lives <= 0) {
    state.lives = 0;
    state.phase = "over";
  } else {
    state.invulnerable = INVULNERABLE_S;
    state.rapid = 0;
  }
}

/** Advance the game by `dt` seconds. `random` returns [0, 1) and is injectable for tests. */
export function stepArcade(state: ArcadeState, input: ArcadeInput, dt: number, random: () => number = Math.random): void {
  // Particles and stars keep drifting on the game-over screen; everything else stops.
  for (const p of state.particles) { p.x += p.vx * dt; p.y += p.vy * dt; p.life -= dt; }
  state.particles = state.particles.filter((p) => p.life > 0);
  state.shake = Math.max(0, state.shake - dt);
  if (state.phase !== "playing" && state.phase !== "over") return;
  const starSpeed = 30 * speedFactor(state.time);
  for (const s of state.stars) {
    s.y += starSpeed * s.depth * dt;
    if (s.y > ARCADE_H) { s.y -= ARCADE_H; s.x = random() * ARCADE_W; }
  }
  if (state.phase !== "playing") return;

  state.time += dt;
  state.score += dt * 10 * arcadeLevel(state);
  state.invulnerable = Math.max(0, state.invulnerable - dt);
  state.rapid = Math.max(0, state.rapid - dt);
  state.cooldown = Math.max(0, state.cooldown - dt);

  // Ship: accelerate towards the held direction, drift to a stop otherwise.
  const ax = (input.right ? 1 : 0) - (input.left ? 1 : 0);
  const ay = (input.down ? 1 : 0) - (input.up ? 1 : 0);
  state.vx += ax * SHIP_ACCEL * dt;
  state.vy += ay * SHIP_ACCEL * dt;
  const drag = Math.pow(SHIP_DRAG, dt);
  if (ax === 0) state.vx *= drag;
  if (ay === 0) state.vy *= drag;
  const speed = Math.hypot(state.vx, state.vy);
  if (speed > SHIP_MAX_SPEED) { state.vx *= SHIP_MAX_SPEED / speed; state.vy *= SHIP_MAX_SPEED / speed; }
  state.shipX += state.vx * dt;
  state.shipY += state.vy * dt;
  const minX = SHIP_RADIUS + 2, maxX = ARCADE_W - SHIP_RADIUS - 2, minY = 40, maxY = ARCADE_H - SHIP_RADIUS - 8;
  if (state.shipX < minX || state.shipX > maxX) { state.shipX = Math.min(maxX, Math.max(minX, state.shipX)); state.vx = 0; }
  if (state.shipY < minY || state.shipY > maxY) { state.shipY = Math.min(maxY, Math.max(minY, state.shipY)); state.vy = 0; }
  // Engine exhaust.
  if (random() < 0.7) {
    const life = 0.2 + random() * 0.2;
    state.particles.push({ x: state.shipX + (random() - 0.5) * 3, y: state.shipY + 9, vx: (random() - 0.5) * 20, vy: 60 + random() * 40, life, max: life, colour: random() < 0.5 ? "#ffd166" : "#ff7a59" });
  }

  // Shooting: twin shots while rapid fire lasts.
  if (input.fire && state.cooldown <= 0) {
    if (state.rapid > 0) state.shots.push({ x: state.shipX - 4, y: state.shipY - 6 }, { x: state.shipX + 4, y: state.shipY - 6 });
    else state.shots.push({ x: state.shipX, y: state.shipY - 9 });
    state.cooldown = state.rapid > 0 ? RAPID_COOLDOWN : SHOT_COOLDOWN;
  }
  for (const s of state.shots) s.y -= SHOT_SPEED * dt;
  state.shots = state.shots.filter((s) => s.y > -6);

  // New rocks.
  state.spawnTimer -= dt;
  while (state.spawnTimer <= 0) {
    spawnRock(state, random);
    state.spawnTimer += spawnInterval(state.time) * (0.7 + random() * 0.6);
  }

  // Rocks move, bounce off the side walls, and leave at the bottom.
  for (const rock of state.rocks) {
    rock.x += rock.vx * dt;
    rock.y += rock.vy * dt;
    rock.angle += rock.spin * dt;
    rock.flash = Math.max(0, rock.flash - dt);
    const r = ROCK_RADIUS[rock.size];
    if (rock.x < r) { rock.x = r; rock.vx = Math.abs(rock.vx); }
    if (rock.x > ARCADE_W - r) { rock.x = ARCADE_W - r; rock.vx = -Math.abs(rock.vx); }
  }

  // Shots against rocks.
  const broken = new Set<Rock>();
  state.shots = state.shots.filter((shot) => {
    for (const rock of state.rocks) {
      if (broken.has(rock)) continue;
      const r = ROCK_RADIUS[rock.size];
      if ((shot.x - rock.x) ** 2 + (shot.y - rock.y) ** 2 <= (r + 1.5) ** 2) {
        rock.hp -= 1;
        rock.flash = 0.08;
        burst(state, shot.x, shot.y, 3, "#fde68a", 50, random);
        if (rock.hp <= 0) broken.add(rock);
        return false;
      }
    }
    return true;
  });
  if (broken.size > 0) {
    state.rocks = state.rocks.filter((rock) => !broken.has(rock));
    for (const rock of broken) breakRock(state, rock, random);
  }
  state.rocks = state.rocks.filter((rock) => rock.y - ROCK_RADIUS[rock.size] < ARCADE_H + 4);

  // Pickups fall slowly and are collected by touch.
  for (const p of state.pickups) { p.y += 45 * dt; p.t += dt; }
  state.pickups = state.pickups.filter((p) => {
    if (Math.hypot(p.x - state.shipX, p.y - state.shipY) <= SHIP_RADIUS + 7) {
      if (p.kind === "shield") state.shield = true;
      else if (p.kind === "rapid") state.rapid = RAPID_S;
      else state.lives = Math.min(MAX_LIVES, state.lives + 1);
      state.score += 50;
      burst(state, p.x, p.y, 12, "#a7f3d0", 70, random);
      return false;
    }
    return p.y < ARCADE_H + 8;
  });

  // A rock hits the ship (slightly forgiving hitbox).
  if (state.invulnerable <= 0) {
    const hit = state.rocks.find((rock) => Math.hypot(rock.x - state.shipX, rock.y - state.shipY) < ROCK_RADIUS[rock.size] * 0.85 + SHIP_RADIUS - 1);
    if (hit) {
      state.rocks = state.rocks.filter((rock) => rock !== hit);
      burst(state, hit.x, hit.y, 12, "#c9b79c", 80, random);
      hitShip(state, random);
    }
  }
}
