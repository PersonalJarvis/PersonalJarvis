/**
 * "City Defense": warheads rain down on six cities and three missile
 * batteries. Aim the crosshair (mouse, or arrow keys) and fire: a
 * counter-missile flies from the nearest battery with ammo to that point and
 * bursts into a blast that grows and fades. Any warhead caught in a blast
 * blows up too, and its blast can catch the next one (chain reactions).
 * Later waves bring warheads that split mid-air and a fast bomber or
 * satellite that drops more. Each wave ends with a bonus for unused ammo and
 * surviving cities; every 10,000 points banks a city that rebuilds a lost
 * one. When the last city falls and none is banked, the run is over.
 */
import type { RetroDrawInfo, RetroGame, RetroInput, RetroStatus } from "../retroGame";
import {
  clamp, drawNumber, drawParticles, drawSprite, drawStars, emitBurst, makeParticles, sprite, starField, stepParticles,
  type Particle,
} from "./games3Kit";

export const WIDTH = 320;
export const HEIGHT = 240;
export const GROUND_Y = 222;
/** The crosshair stays in the sky. */
const AIM_TOP = 10;
const AIM_BOTTOM = 196;
const AIM_SPEED = 170;

export const CITY_X: readonly number[] = [64, 92, 120, 200, 228, 256];
export const BATTERY_X: readonly number[] = [24, 160, 296];
/** Where counter-missiles leave a battery (the top of its mound). */
export const BATTERY_Y = 204;
export const AMMO = 10;
/** The centre battery fires faster missiles. */
const MISSILE_SPEED = [170, 260, 170] as const;

const BLAST_GROW = 0.45;
const BLAST_HOLD = 0.25;
const BLAST_SHRINK = 0.45;
export const BLAST_S = BLAST_GROW + BLAST_HOLD + BLAST_SHRINK;
export const BLAST_R = 16;
/** A warhead destroyed in the air bursts into a slightly smaller blast of its own. */
export const CHAIN_R = 13;
const IMPACT_R = 11;
/** How close to a city or battery an impact must be to wreck it. */
const CITY_HIT = 12;
const BATTERY_HIT = 14;

const MAX_MISSILES = 10;
const MAX_WARHEADS = 40;
const MAX_BLASTS = 48;

const WARHEAD_POINTS = 25;
const FLYER_POINTS = 100;
const AMMO_BONUS = 5;
const CITY_BONUS = 100;
export const BONUS_CITY_EVERY = 10000;
const READY_S = 1.6;
const BONUS_S = 2.6;
const LOST_S = 2.2;

export type DefensePhase = "ready" | "play" | "bonus" | "lost" | "over";

export interface Battery { x: number; ammo: number; alive: boolean }
export interface Counter { active: boolean; ox: number; oy: number; x: number; y: number; tx: number; ty: number; vx: number; vy: number; left: number }
export interface Warhead { active: boolean; ox: number; oy: number; x: number; y: number; vx: number; vy: number; ty: number; mirv: boolean; splitY: number }
/** A blast: `friendly` ones (ours and chains) destroy what they touch in the air; impacts only wreck the ground. */
export interface Blast { active: boolean; x: number; y: number; age: number; r: number; friendly: boolean }
export interface Flyer { active: boolean; kind: 0 | 1; x: number; y: number; dir: 1 | -1; speed: number; dropTimer: number }

export interface DefenseState {
  phase: DefensePhase;
  timer: number;
  /** Total seconds simulated; zero means the run has not started (attract screen). */
  elapsed: number;
  score: number;
  wave: number;
  nextCityAt: number;
  /** Bonus cities waiting to rebuild a destroyed one at the next wave. */
  banked: number;
  cx: number;
  cy: number;
  /** Last pointer position seen, so a resting mouse does not undo keyboard aiming. */
  lastPx: number;
  lastPy: number;
  /** The battery forced with `b`, or -1 for "nearest with ammo". */
  selected: number;
  cities: Uint8Array;
  batteries: Battery[];
  missiles: Counter[];
  warheads: Warhead[];
  blasts: Blast[];
  flyer: Flyer;
  /** Warheads (including the flyer's drops) still to come this wave. */
  toLaunch: number;
  salvoTimer: number;
  flyerTimer: number;
  /** The last wave's bonus tally, for the bonus screen. */
  bonusAmmo: number;
  bonusCities: number;
  particles: Particle[];
  shake: number;
}

/** Score multiplier: ×1 for waves 1–2, ×2 for 3–4 … up to ×6. */
export function multiplier(wave: number): number {
  return Math.min(6, 1 + Math.floor((wave - 1) / 2));
}

export function warheadSpeed(wave: number): number {
  return Math.min(70, 16 + wave * 4);
}

function wavePayload(wave: number): number {
  return Math.min(36, 10 + wave * 2);
}

/** Radius of a blast at a given age: it grows, holds and shrinks away. */
export function blastRadius(age: number, max: number): number {
  if (age < BLAST_GROW) return (max * age) / BLAST_GROW;
  if (age < BLAST_GROW + BLAST_HOLD) return max;
  return Math.max(0, max * (1 - (age - BLAST_GROW - BLAST_HOLD) / BLAST_SHRINK));
}

function startWave(state: DefenseState, random: () => number): void {
  state.phase = "ready";
  state.timer = READY_S;
  for (const b of state.batteries) { b.alive = true; b.ammo = AMMO; }
  for (const m of state.missiles) m.active = false;
  for (const w of state.warheads) w.active = false;
  for (const b of state.blasts) b.active = false;
  state.flyer.active = false;
  state.toLaunch = wavePayload(state.wave);
  state.salvoTimer = 0.4;
  state.flyerTimer = 8 + random() * 8;
  state.selected = -1;
}

function create(random: () => number): DefenseState {
  const state: DefenseState = {
    phase: "ready", timer: 0, elapsed: 0, score: 0, wave: 1, nextCityAt: BONUS_CITY_EVERY, banked: 0,
    cx: WIDTH / 2, cy: 110, lastPx: -1, lastPy: -1, selected: -1,
    cities: new Uint8Array(CITY_X.length).fill(1),
    batteries: BATTERY_X.map((x) => ({ x, ammo: AMMO, alive: true })),
    missiles: Array.from({ length: MAX_MISSILES }, () => ({ active: false, ox: 0, oy: 0, x: 0, y: 0, tx: 0, ty: 0, vx: 0, vy: 0, left: 0 })),
    warheads: Array.from({ length: MAX_WARHEADS }, () => ({ active: false, ox: 0, oy: 0, x: 0, y: 0, vx: 0, vy: 0, ty: 0, mirv: false, splitY: 0 })),
    blasts: Array.from({ length: MAX_BLASTS }, () => ({ active: false, x: 0, y: 0, age: 0, r: 0, friendly: false })),
    flyer: { active: false, kind: 0, x: 0, y: 0, dir: 1, speed: 0, dropTimer: 0 },
    toLaunch: 0, salvoTimer: 0, flyerTimer: 0, bonusAmmo: 0, bonusCities: 0,
    particles: makeParticles(160),
    shake: 0,
  };
  startWave(state, random);
  return state;
}

function addScore(state: DefenseState, points: number): void {
  state.score += points;
  while (state.score >= state.nextCityAt) {
    state.banked += 1;
    state.nextCityAt += BONUS_CITY_EVERY;
  }
}

function spawnBlast(state: DefenseState, x: number, y: number, r: number, friendly: boolean): void {
  let slot: Blast | undefined;
  for (const b of state.blasts) if (!b.active) { slot = b; break; }
  if (!slot) return;
  slot.active = true; slot.x = x; slot.y = y; slot.age = 0; slot.r = r; slot.friendly = friendly;
}

/**
 * Send a warhead from (x, y) to the ground at `tx` (to the battery top when it
 * aims at a battery). Returns false when the sky is full.
 */
export function addWarhead(state: DefenseState, x: number, y: number, tx: number, ty: number, speed: number, mirv: boolean, splitY = 0): boolean {
  let w: Warhead | undefined;
  for (const c of state.warheads) if (!c.active) { w = c; break; }
  if (!w) return false;
  const dx = tx - x, dy = ty - y;
  const len = Math.max(1, Math.hypot(dx, dy));
  w.active = true; w.ox = x; w.oy = y; w.x = x; w.y = y;
  w.vx = (dx / len) * speed; w.vy = (dy / len) * speed;
  w.ty = ty; w.mirv = mirv; w.splitY = splitY;
  return true;
}

/** A target on the ground: a standing city or battery most of the time, sometimes open ground. */
function pickTarget(state: DefenseState, random: () => number, out: number[]): void {
  let standing = 0;
  for (const c of state.cities) standing += c;
  for (const b of state.batteries) if (b.alive) standing += 1;
  if (standing === 0 || random() < 0.15) {
    out[0] = 12 + random() * (WIDTH - 24);
    out[1] = GROUND_Y;
    return;
  }
  let pick = Math.floor(random() * standing);
  for (let i = 0; i < CITY_X.length; i += 1) {
    if (state.cities[i] && pick-- === 0) { out[0] = CITY_X[i]; out[1] = GROUND_Y; return; }
  }
  for (const b of state.batteries) {
    if (b.alive && pick-- === 0) { out[0] = b.x; out[1] = BATTERY_Y + 4; return; }
  }
}
const target = [0, 0];

function launchFromSky(state: DefenseState, random: () => number): void {
  pickTarget(state, random, target);
  const mirvChance = state.wave >= 3 ? Math.min(0.35, 0.06 * (state.wave - 2)) : 0;
  const mirv = random() < mirvChance;
  if (addWarhead(state, 8 + random() * (WIDTH - 16), 0, target[0], target[1], warheadSpeed(state.wave), mirv, 50 + random() * 70)) {
    state.toLaunch -= 1;
  }
}

/** Split a MIRV: two or three new warheads leave from where it is now; the original flies on. */
function split(state: DefenseState, w: Warhead, random: () => number): void {
  w.mirv = false;
  const count = 2 + (random() < 0.4 ? 1 : 0);
  const speed = Math.hypot(w.vx, w.vy);
  for (let i = 0; i < count; i += 1) {
    pickTarget(state, random, target);
    addWarhead(state, w.x, w.y, target[0], target[1], speed, false);
  }
}

/** The battery that would fire at the crosshair now: the forced one if it can, else the nearest with ammo; -1 if none. */
export function firingBattery(state: DefenseState): number {
  const forced = state.selected >= 0 ? state.batteries[state.selected] : null;
  if (forced && forced.alive && forced.ammo > 0) return state.selected;
  let best = -1, bestD = Infinity;
  for (let i = 0; i < state.batteries.length; i += 1) {
    const b = state.batteries[i];
    if (!b.alive || b.ammo <= 0) continue;
    // Ties go to the centre battery, whose missiles are faster.
    const d = Math.abs(b.x - state.cx) - (i === 1 ? 0.5 : 0);
    if (d < bestD) { bestD = d; best = i; }
  }
  return best;
}

function fire(state: DefenseState): boolean {
  const i = firingBattery(state);
  if (i < 0) return false;
  let m: Counter | undefined;
  for (const c of state.missiles) if (!c.active) { m = c; break; }
  if (!m) return false;
  const b = state.batteries[i];
  b.ammo -= 1;
  const dx = state.cx - b.x, dy = state.cy - BATTERY_Y;
  const len = Math.max(1, Math.hypot(dx, dy));
  m.active = true; m.ox = b.x; m.oy = BATTERY_Y; m.x = b.x; m.y = BATTERY_Y;
  m.tx = state.cx; m.ty = state.cy; m.left = len;
  m.vx = (dx / len) * MISSILE_SPEED[i]; m.vy = (dy / len) * MISSILE_SPEED[i];
  return true;
}

/** Cycle the forced battery: nearest → left → centre → right → nearest, skipping empty ones. */
function cycleBattery(state: DefenseState): void {
  for (let n = 0; n < 4; n += 1) {
    state.selected = state.selected >= 2 ? -1 : state.selected + 1;
    if (state.selected < 0) return;
    const b = state.batteries[state.selected];
    if (b.alive && b.ammo > 0) return;
  }
}

/** A warhead reached the ground: wreck what stands there. */
function impact(state: DefenseState, x: number, y: number, random: () => number): void {
  spawnBlast(state, x, y, IMPACT_R, false);
  emitBurst(state.particles, x, y, 12, 60, "#ff7a3a", random, 0.7);
  for (let i = 0; i < CITY_X.length; i += 1) {
    if (state.cities[i] && Math.abs(CITY_X[i] - x) <= CITY_HIT) {
      state.cities[i] = 0;
      state.shake = 0.35;
      emitBurst(state.particles, CITY_X[i], GROUND_Y - 4, 24, 80, "#7ff0ff", random, 1);
    }
  }
  for (const b of state.batteries) {
    if (b.alive && Math.abs(b.x - x) <= BATTERY_HIT) {
      b.alive = false;
      b.ammo = 0;
      state.shake = 0.35;
      emitBurst(state.particles, b.x, BATTERY_Y, 20, 70, "#ffd35a", random, 0.9);
    }
  }
}

export function citiesStanding(state: DefenseState): number {
  let n = 0;
  for (const c of state.cities) n += c;
  return n;
}

/** Everything in the air moves; blasts grow, catch warheads and fade. */
function stepWorld(state: DefenseState, dt: number, random: () => number): void {
  const mult = multiplier(state.wave);
  for (const m of state.missiles) {
    if (!m.active) continue;
    const travel = Math.hypot(m.vx, m.vy) * dt;
    if (travel >= m.left) {
      m.active = false;
      spawnBlast(state, m.tx, m.ty, BLAST_R, true);
      continue;
    }
    m.left -= travel;
    m.x += m.vx * dt;
    m.y += m.vy * dt;
  }
  for (const w of state.warheads) {
    if (!w.active) continue;
    w.x += w.vx * dt;
    w.y += w.vy * dt;
    if (w.mirv && w.y >= w.splitY) split(state, w, random);
    if (w.y >= w.ty) {
      w.active = false;
      impact(state, w.x, w.ty, random);
    }
  }
  const f = state.flyer;
  if (f.active) {
    f.x += f.dir * f.speed * dt;
    if (f.x < -24 || f.x > WIDTH + 24) f.active = false;
    else if ((f.dropTimer -= dt) <= 0 && state.toLaunch > 0 && f.x > 16 && f.x < WIDTH - 16) {
      pickTarget(state, random, target);
      if (addWarhead(state, f.x, f.y + 4, target[0], target[1], warheadSpeed(state.wave), false)) state.toLaunch -= 1;
      f.dropTimer = 1.1 + random() * 1.2;
    }
  }
  for (const b of state.blasts) {
    if (!b.active) continue;
    b.age += dt;
    if (b.age >= BLAST_S) { b.active = false; continue; }
    if (!b.friendly) continue;
    const r = blastRadius(b.age, b.r);
    const r2 = r * r;
    for (const w of state.warheads) {
      if (!w.active) continue;
      const dx = w.x - b.x, dy = w.y - b.y;
      if (dx * dx + dy * dy <= r2) {
        w.active = false;
        addScore(state, WARHEAD_POINTS * mult);
        spawnBlast(state, w.x, w.y, CHAIN_R, true);
        emitBurst(state.particles, w.x, w.y, 8, 50, "#ffe9a8", random, 0.5);
      }
    }
    if (f.active) {
      const dx = f.x - b.x, dy = f.y - b.y;
      if (dx * dx + dy * dy <= (r + 5) * (r + 5)) {
        f.active = false;
        addScore(state, FLYER_POINTS * mult);
        spawnBlast(state, f.x, f.y, BLAST_R, true);
        emitBurst(state.particles, f.x, f.y, 20, 80, "#c8f0ff", random, 0.8);
      }
    }
  }
}

function airborne(state: DefenseState): boolean {
  if (state.flyer.active) return true;
  for (const m of state.missiles) if (m.active) return true;
  for (const w of state.warheads) if (w.active) return true;
  for (const b of state.blasts) if (b.active) return true;
  return false;
}

function endWave(state: DefenseState): void {
  const mult = multiplier(state.wave);
  let ammo = 0;
  for (const b of state.batteries) ammo += b.ammo;
  state.bonusAmmo = ammo;
  state.bonusCities = citiesStanding(state);
  addScore(state, (ammo * AMMO_BONUS + state.bonusCities * CITY_BONUS) * mult);
  state.phase = "bonus";
  state.timer = BONUS_S;
}

function aim(state: DefenseState, input: RetroInput, dt: number): void {
  const p = input.pointer;
  if (p && (p.x !== state.lastPx || p.y !== state.lastPy || p.clicked)) {
    state.cx = clamp(p.x, 4, WIDTH - 4);
    state.cy = clamp(p.y, AIM_TOP, AIM_BOTTOM);
    state.lastPx = p.x;
    state.lastPy = p.y;
  }
  const dx = (input.right ? 1 : 0) - (input.left ? 1 : 0);
  const dy = (input.down ? 1 : 0) - (input.up ? 1 : 0);
  if (dx || dy) {
    state.cx = clamp(state.cx + dx * AIM_SPEED * dt, 4, WIDTH - 4);
    state.cy = clamp(state.cy + dy * AIM_SPEED * dt, AIM_TOP, AIM_BOTTOM);
  }
}

function step(state: DefenseState, input: RetroInput, dt: number, random: () => number): void {
  stepParticles(state.particles, dt, 60);
  if (state.phase === "over") return;
  state.elapsed += dt;
  state.shake = Math.max(0, state.shake - dt);
  aim(state, input, dt);

  if (state.phase === "ready") {
    if ((state.timer -= dt) <= 0) state.phase = "play";
    return;
  }
  if (state.phase === "bonus") {
    if ((state.timer -= dt) <= 0) {
      // Banked cities rebuild lost ones; with none standing and none banked the run is over.
      for (let i = 0; i < state.cities.length && state.banked > 0; i += 1) {
        if (!state.cities[i]) { state.cities[i] = 1; state.banked -= 1; }
      }
      if (citiesStanding(state) === 0) {
        state.phase = "over";
        return;
      }
      state.wave += 1;
      startWave(state, random);
    }
    return;
  }

  stepWorld(state, dt, random);
  if (state.phase === "lost") {
    if ((state.timer -= dt) <= 0) state.phase = "over";
    return;
  }

  if (input.pressed.b) cycleBattery(state);
  if (input.pressed.a || input.pointer?.clicked) fire(state);

  // The attack: salvoes from the top, and now and then a flyer.
  if (state.toLaunch > 0) {
    if ((state.salvoTimer -= dt) <= 0) {
      const size = Math.min(state.toLaunch, 1 + Math.floor(random() * Math.min(4, 1 + state.wave / 2)));
      for (let i = 0; i < size; i += 1) launchFromSky(state, random);
      state.salvoTimer = Math.max(1.2, 3.4 - state.wave * 0.2) + random() * 0.8;
    }
    if (state.wave >= 2 && !state.flyer.active && (state.flyerTimer -= dt) <= 0) {
      const f = state.flyer;
      f.active = true;
      f.kind = random() < 0.5 ? 0 : 1;
      f.dir = random() < 0.5 ? 1 : -1;
      f.x = f.dir > 0 ? -16 : WIDTH + 16;
      f.y = 44 + random() * 50;
      f.speed = f.kind === 0 ? 58 : 76;
      f.dropTimer = 0.8 + random();
      state.flyerTimer = 12 + random() * 10;
    }
  }

  if (citiesStanding(state) === 0 && state.banked === 0) {
    state.phase = "lost";
    state.timer = LOST_S;
    return;
  }
  if (state.toLaunch <= 0 && !airborne(state)) endWave(state);
}

function status(state: DefenseState): RetroStatus {
  return { score: state.score, lives: citiesStanding(state), level: state.wave, over: state.phase === "over" };
}

// ---- Drawing --------------------------------------------------------------

const SKY = ["#05061a", "#080a24", "#0c0d2e", "#120f36", "#1a1240", "#24154a"] as const;
const STARS = starField(60, WIDTH, 150, 23);
const CITY_SPRITES = [
  sprite(["....##..............", "....##.....###......", ".####.#...####..##..", ".####.##..####.###..", "#########.########.#", "####################", "####################", "####################"]),
  sprite([".........##.........", "..###....##...##....", "..###...####..##.##.", "######..####.#######", "######.#############", "####################", "####################", "####################"]),
  sprite(["...##...............", "...##.......####....", "######.....######...", "######.##..######.##", "#########.##########", "####################", "####################", "####################"]),
];
const RUBBLE = sprite(["......#.......#.....", "..#..###...#.###....", ".####.####.#######..", "####################"]);
const BOMBER = sprite(["......##........", ".....####.......", "################", ".##############.", "....######......", ".....##........."]);
const SATELLITE = sprite(["###.......###", "###..###..###", "#############", "###..###..###", "###.......###"]);

function drawSky(ctx: CanvasRenderingContext2D): void {
  const band = Math.ceil(GROUND_Y / SKY.length);
  for (let i = 0; i < SKY.length; i += 1) {
    ctx.fillStyle = SKY[i];
    ctx.fillRect(0, i * band, WIDTH, band);
  }
}

function drawGround(ctx: CanvasRenderingContext2D, state: DefenseState): void {
  ctx.fillStyle = "#6b4a1e";
  ctx.fillRect(0, GROUND_Y, WIDTH, HEIGHT - GROUND_Y);
  ctx.fillStyle = "#d4a843";
  ctx.fillRect(0, GROUND_Y, WIDTH, 1);
  for (const b of state.batteries) {
    // A stepped mound under each battery.
    for (let s = 0; s < 4; s += 1) {
      const half = 22 - s * 4;
      ctx.fillStyle = s === 3 ? "#d4a843" : "#8c6a2a";
      ctx.fillRect(b.x - half, GROUND_Y - (s + 1) * 4, half * 2, 4);
    }
  }
}

function drawBatteries(ctx: CanvasRenderingContext2D, state: DefenseState, info: RetroDrawInfo): void {
  for (const b of state.batteries) {
    if (!b.alive) {
      ctx.fillStyle = "#2a1a0c";
      ctx.fillRect(b.x - 8, BATTERY_Y + 2, 16, 3);
      continue;
    }
    // Ammo as rows of little missiles: 4, 3, 2, 1 from the bottom.
    ctx.fillStyle = "#7ff0ff";
    let left = b.ammo;
    for (let row = 0; row < 4 && left > 0; row += 1) {
      const n = 4 - row;
      for (let k = 0; k < n && left > 0; k += 1, left -= 1) ctx.fillRect(b.x - n * 3 + k * 6 + 2, BATTERY_Y + 12 - row * 4 - 3, 2, 3);
    }
  }
  const firing = firingBattery(state);
  if (firing >= 0 && state.phase !== "over") {
    const x = state.batteries[firing].x;
    const bob = info.reduced ? 0 : Math.round(Math.sin(info.time * 5));
    ctx.fillStyle = state.selected >= 0 ? "#ffd35a" : "#9aa3c7";
    ctx.fillRect(x - 2, BATTERY_Y - 10 + bob, 5, 1);
    ctx.fillRect(x - 1, BATTERY_Y - 9 + bob, 3, 1);
    ctx.fillRect(x, BATTERY_Y - 8 + bob, 1, 1);
  }
}

function drawCities(ctx: CanvasRenderingContext2D, state: DefenseState): void {
  for (let i = 0; i < CITY_X.length; i += 1) {
    const x = CITY_X[i] - 10;
    if (state.cities[i]) {
      ctx.fillStyle = "#3fb6d9";
      drawSprite(ctx, CITY_SPRITES[i % CITY_SPRITES.length], x, GROUND_Y - 8);
      // Lit windows, a fixed pattern per city.
      ctx.fillStyle = "#ffe08a";
      for (let k = 0; k < 6; k += 1) ctx.fillRect(x + 2 + ((k * 7 + i * 3) % 16), GROUND_Y - 4 + (k % 2) * 2, 1, 1);
    } else {
      ctx.fillStyle = "#5a3328";
      drawSprite(ctx, RUBBLE, x, GROUND_Y - 4);
    }
  }
}

function drawCross(ctx: CanvasRenderingContext2D, x: number, y: number, size: number): void {
  ctx.fillRect(x - size, y, size - 1, 1);
  ctx.fillRect(x + 2, y, size - 1, 1);
  ctx.fillRect(x, y - size, 1, size - 1);
  ctx.fillRect(x, y + 2, 1, size - 1);
}

function draw(ctx: CanvasRenderingContext2D, state: DefenseState, info: RetroDrawInfo): void {
  drawSky(ctx);
  drawStars(ctx, STARS, info.time, info.reduced);

  ctx.save();
  if (state.shake > 0 && !info.reduced) {
    ctx.translate(Math.round(Math.sin(info.time * 77) * state.shake * 6), Math.round(Math.cos(info.time * 91) * state.shake * 4));
  }
  drawGround(ctx, state);
  drawCities(ctx, state);
  drawBatteries(ctx, state, info);

  // Trails: incoming in red, ours in blue.
  ctx.lineWidth = 1;
  ctx.strokeStyle = "#ff5a4a";
  ctx.globalAlpha = 0.75;
  ctx.beginPath();
  for (const w of state.warheads) {
    if (!w.active) continue;
    ctx.moveTo(w.ox, w.oy);
    ctx.lineTo(w.x, w.y);
  }
  ctx.stroke();
  ctx.strokeStyle = "#5ab8ff";
  ctx.beginPath();
  for (const m of state.missiles) {
    if (!m.active) continue;
    ctx.moveTo(m.ox, m.oy);
    ctx.lineTo(m.x, m.y);
  }
  ctx.stroke();
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#fff4d0";
  for (const w of state.warheads) if (w.active) ctx.fillRect(Math.round(w.x) - 1, Math.round(w.y) - 1, 2, 2);
  for (const m of state.missiles) {
    if (!m.active) continue;
    ctx.fillStyle = "#e6f6ff";
    ctx.fillRect(Math.round(m.x) - 1, Math.round(m.y) - 1, 2, 2);
    ctx.fillStyle = "#5ab8ff";
    // The target mark: a small x where the missile will burst.
    const tx = Math.round(m.tx), ty = Math.round(m.ty);
    for (let k = -2; k <= 2; k += 1) {
      ctx.fillRect(tx + k, ty + k, 1, 1);
      ctx.fillRect(tx + k, ty - k, 1, 1);
    }
  }

  const f = state.flyer;
  if (f.active) {
    ctx.fillStyle = f.kind === 0 ? "#c8d0e0" : "#ffd35a";
    if (f.kind === 0) drawSprite(ctx, BOMBER, f.x - 8, f.y - 3, 1, f.dir < 0);
    else drawSprite(ctx, SATELLITE, f.x - 6, f.y - 2);
  }

  // Blasts: a warm outer ball with a hot core; steady colours (no strobing).
  for (const b of state.blasts) {
    if (!b.active) continue;
    const r = blastRadius(b.age, b.r);
    if (r <= 0.5) continue;
    ctx.fillStyle = b.friendly ? "#ff9f43" : "#ff5a3a";
    ctx.globalAlpha = 0.85;
    ctx.beginPath();
    ctx.arc(b.x, b.y, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = b.friendly ? "#fff3c4" : "#ffd27a";
    ctx.beginPath();
    ctx.arc(b.x, b.y, r * 0.55, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
  }
  drawParticles(ctx, state.particles);
  ctx.restore();

  // Crosshair; on the attract screen it drifts lazily across the sky.
  let ax = state.cx, ay = state.cy;
  if (info.idle && state.elapsed === 0 && !info.reduced) {
    ax = WIDTH / 2 + Math.sin(info.time * 0.7) * 90;
    ay = 100 + Math.sin(info.time * 1.1) * 40;
  }
  ctx.fillStyle = "#ffffff";
  drawCross(ctx, Math.round(ax), Math.round(ay), 6);

  // Score, wave and banked cities.
  ctx.fillStyle = "#ffcf5a";
  drawNumber(ctx, state.score, 6, 5, 2);
  ctx.fillStyle = "#ff8a6a";
  drawNumber(ctx, state.wave, WIDTH - 6, 5, 2, "right");
  ctx.fillStyle = "#3fb6d9";
  for (let i = 0; i < Math.min(state.banked, 5); i += 1) ctx.fillRect(WIDTH - 10 - i * 6, 18, 4, 3);

  if (state.phase === "ready") {
    ctx.fillStyle = "#ffcf5a";
    drawNumber(ctx, state.wave, WIDTH / 2, 80, 5, "center");
    const mult = multiplier(state.wave);
    // "×n" as a pixel cross and the digit.
    ctx.fillStyle = "#ff8a6a";
    for (let k = 0; k < 5; k += 1) {
      ctx.fillRect(WIDTH / 2 - 10 + k * 2, 112 + k * 2, 2, 2);
      ctx.fillRect(WIDTH / 2 - 2 - k * 2, 112 + k * 2, 2, 2);
    }
    drawNumber(ctx, mult, WIDTH / 2 + 4, 112, 2);
  } else if (state.phase === "bonus") {
    // The tally: missiles left and cities saved, each with its points.
    const mult = multiplier(state.wave);
    ctx.fillStyle = "#7ff0ff";
    ctx.fillRect(WIDTH / 2 - 60, 84, 2, 5);
    drawNumber(ctx, state.bonusAmmo, WIDTH / 2 - 50, 84, 1);
    ctx.fillStyle = "#ffcf5a";
    drawNumber(ctx, state.bonusAmmo * AMMO_BONUS * mult, WIDTH / 2 + 60, 84, 1, "right");
    ctx.fillStyle = "#3fb6d9";
    ctx.fillRect(WIDTH / 2 - 62, 100, 6, 5);
    drawNumber(ctx, state.bonusCities, WIDTH / 2 - 50, 100, 1);
    ctx.fillStyle = "#ffcf5a";
    drawNumber(ctx, state.bonusCities * CITY_BONUS * mult, WIDTH / 2 + 60, 100, 1, "right");
  }
}

const cityDefense: RetroGame<DefenseState> = {
  id: "city-defense",
  width: WIDTH,
  height: HEIGHT,
  create,
  step,
  draw,
  status,
  pointer: true,
};

export default cityDefense;
