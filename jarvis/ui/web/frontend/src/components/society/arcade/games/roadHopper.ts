/**
 * "Road Hopper": hop a little frog across five lanes of traffic and a river
 * to the five home bays in the hedge at the top. One hop per press (holding a
 * direction does not keep hopping). On the river only logs and turtles carry
 * you; the water, a diving turtle or being carried off the edge cost a life,
 * and so does the clock running out. Filling all five bays starts the next
 * level with faster lanes.
 *
 * The lanes never store moving objects: each lane is a fixed pattern of
 * items plus one scrolling `shift`, so the world costs no allocations.
 */
import type { RetroDrawInfo, RetroGame, RetroInput, RetroStatus } from "../retroGame";
import {
  clamp, drawNumber, drawParticles, drawSprite, emitBurst, makeParticles, sprite, stepParticles,
  type Particle, type PixelSprite,
} from "./games3Kit";

export const WIDTH = 224;
export const HEIGHT = 256;
export const CELL = 16;

export const HOME_ROW = 1;
export const START_ROW = 13;
export const START_X = WIDTH / 2;
/** Rows 2–6 are river, row 7 the middle bank, rows 8–12 road. */
export const RIVER_TOP = 2;
export const RIVER_BOTTOM = 6;
export const ROAD_TOP = 8;
export const ROAD_BOTTOM = 12;

export const HOP_S = 0.1;
export const LIFE_TIME = 30;
const DEATH_S = 1.2;
const LEVEL_S = 2;
/** Half the frog's width for traffic; a river platform must hold its centre. */
const FROG_HALF = 6;
export const BAY_X: readonly number[] = [24, 68, 112, 156, 200];
/** How far off a bay's centre a landing still counts. */
const BAY_CATCH = 9;
const FLY_BONUS = 200;
const BAY_POINTS = 50;
const LEVEL_POINTS = 1000;
/** Items are placed so they start fully off the left edge; any item is at most this long. */
const MARGIN = 96;

export type LaneKind = "log" | "turtle" | "truck" | "car" | "racer" | "dozer";

export interface LaneDef {
  row: number;
  kind: LaneKind;
  /** Pixels per second at level 1; positive moves right. */
  speed: number;
  /** Length of one repeat of the pattern; must be at least WIDTH + MARGIN. */
  period: number;
  offsets: readonly number[];
  lens: readonly number[];
  /** Turtle groups that dive now and then. */
  dives?: readonly boolean[];
}

export const LANES: readonly LaneDef[] = [
  { row: 2, kind: "log", speed: 34, period: 336, offsets: [0, 112, 224], lens: [64, 64, 64] },
  { row: 3, kind: "turtle", speed: -40, period: 320, offsets: [0, 80, 160, 240], lens: [32, 32, 32, 32], dives: [false, true, false, false] },
  { row: 4, kind: "log", speed: 52, period: 400, offsets: [0, 200], lens: [96, 96] },
  { row: 5, kind: "log", speed: 28, period: 336, offsets: [0, 112, 224], lens: [48, 48, 48] },
  { row: 6, kind: "turtle", speed: -32, period: 336, offsets: [0, 112, 224], lens: [48, 48, 48], dives: [false, false, true] },
  { row: 8, kind: "truck", speed: -30, period: 336, offsets: [0, 168], lens: [32, 32] },
  { row: 9, kind: "racer", speed: 72, period: 336, offsets: [0, 168], lens: [16, 16] },
  { row: 10, kind: "car", speed: -40, period: 336, offsets: [0, 112, 224], lens: [16, 16, 16] },
  { row: 11, kind: "dozer", speed: 28, period: 336, offsets: [0, 112, 224], lens: [16, 16, 16] },
  { row: 12, kind: "car", speed: -34, period: 320, offsets: [0, 80, 160, 240], lens: [16, 16, 16, 16] },
];
const LANE_OF_ROW: readonly number[] = Array.from({ length: 16 }, (_, row) => LANES.findIndex((l) => l.row === row));

/** Turtle dive cycle in seconds, and when (within it) a diving group sinks, is under, and comes back. */
const DIVE_CYCLE = 5;
const DIVE_SINK = 3;
const DIVE_UNDER = 3.6;
const DIVE_RISE = 4.8;

export type HopperPhase = "play" | "dying" | "levelup" | "over";
export type Direction = 0 | 1 | 2 | 3; // up, right, down, left
export type DeathKind = "splat" | "splash" | "time";

export interface Hop { active: boolean; t: number; fromX: number; fromRow: number; toX: number; toRow: number }
interface Popup { active: boolean; x: number; y: number; t: number; value: number }

export interface HopperState {
  phase: HopperPhase;
  timer: number;
  /** Total seconds simulated; zero means the run has not started (attract screen). */
  elapsed: number;
  score: number;
  lives: number;
  level: number;
  /** The frog's centre x in pixels and its row. */
  x: number;
  row: number;
  facing: Direction;
  hop: Hop;
  /** A direction pressed during a hop, taken as soon as the frog lands; -1 for none. */
  queued: number;
  /** The highest row (smallest index) reached by this frog; new rows score. */
  bestRow: number;
  timeLeft: number;
  bays: Uint8Array;
  flyBay: number;
  flyLeft: number;
  flyTimer: number;
  /** Scroll of each lane's pattern, parallel to LANES. */
  shifts: number[];
  diveClock: number;
  deathKind: DeathKind;
  particles: Particle[];
  popups: Popup[];
}

/** Lane speeds grow 20 % per level, up to a little over double. */
export function levelFactor(level: number): number {
  return Math.min(2.2, 1 + 0.2 * (level - 1));
}

function mod(a: number, m: number): number {
  return ((a % m) + m) % m;
}

/** Left edge of item `i` of lane `lane` for a given scroll. */
export function itemX(lane: number, i: number, shift: number): number {
  const def = LANES[lane];
  return mod(def.offsets[i] + shift, def.period) - MARGIN;
}

/** 0 surfaced, 1 sinking or rising (still rideable), 2 under water. */
export function diveState(lane: number, i: number, clock: number): 0 | 1 | 2 {
  if (!LANES[lane].dives?.[i]) return 0;
  const t = mod(clock + i * 1.3, DIVE_CYCLE);
  return t < DIVE_SINK ? 0 : t < DIVE_UNDER ? 1 : t < DIVE_RISE ? 2 : 1;
}

/** Whether a frog centred at x on a river row stands on something that floats. */
export function platformAt(state: HopperState, row: number, x: number): boolean {
  const lane = LANE_OF_ROW[row];
  if (lane < 0) return false;
  const def = LANES[lane];
  for (let i = 0; i < def.offsets.length; i += 1) {
    const x0 = itemX(lane, i, state.shifts[lane]);
    if (x >= x0 + 1 && x <= x0 + def.lens[i] - 1 && diveState(lane, i, state.diveClock) !== 2) return true;
  }
  return false;
}

/** Whether a frog centred at x on a road row touches a vehicle. */
export function vehicleAt(state: HopperState, row: number, x: number): boolean {
  const lane = LANE_OF_ROW[row];
  if (lane < 0) return false;
  const def = LANES[lane];
  for (let i = 0; i < def.offsets.length; i += 1) {
    const x0 = itemX(lane, i, state.shifts[lane]);
    if (x + FROG_HALF > x0 + 1 && x - FROG_HALF < x0 + def.lens[i] - 1) return true;
  }
  return false;
}

export function isRiver(row: number): boolean {
  return row >= RIVER_TOP && row <= RIVER_BOTTOM;
}

function isRoad(row: number): boolean {
  return row >= ROAD_TOP && row <= ROAD_BOTTOM;
}

function respawn(state: HopperState): void {
  state.phase = "play";
  state.x = START_X;
  state.row = START_ROW;
  state.facing = 0;
  state.hop.active = false;
  state.queued = -1;
  state.bestRow = START_ROW;
  state.timeLeft = LIFE_TIME;
}

function create(random: () => number): HopperState {
  const state: HopperState = {
    phase: "play", timer: 0, elapsed: 0, score: 0, lives: 3, level: 1,
    x: START_X, row: START_ROW, facing: 0,
    hop: { active: false, t: 0, fromX: 0, fromRow: 0, toX: 0, toRow: 0 },
    queued: -1, bestRow: START_ROW, timeLeft: LIFE_TIME,
    bays: new Uint8Array(5), flyBay: -1, flyLeft: 0, flyTimer: 6 + random() * 5,
    shifts: LANES.map((l) => Math.floor(random() * l.period)),
    diveClock: 0, deathKind: "splat",
    particles: makeParticles(80),
    popups: Array.from({ length: 3 }, () => ({ active: false, x: 0, y: 0, t: 0, value: 0 })),
  };
  respawn(state);
  return state;
}

function popup(state: HopperState, x: number, y: number, value: number): void {
  const p = state.popups.find((q) => !q.active) ?? state.popups[0];
  p.active = true; p.x = x; p.y = y; p.t = 1.2; p.value = value;
}

function die(state: HopperState, kind: DeathKind, random: () => number): void {
  state.phase = "dying";
  state.timer = DEATH_S;
  state.deathKind = kind;
  state.lives = Math.max(0, state.lives - 1);
  state.hop.active = false;
  state.queued = -1;
  const cy = state.row * CELL + CELL / 2;
  if (kind === "splash") emitBurst(state.particles, state.x, cy, 16, 50, "#9fd8ff", random, 0.7);
  else emitBurst(state.particles, state.x, cy, 14, 45, "#7ed957", random, 0.6);
}

function startHop(state: HopperState, dir: Direction): void {
  state.facing = dir;
  let toRow = state.row, toX = state.x;
  if (dir === 0) toRow = Math.max(HOME_ROW, state.row - 1);
  else if (dir === 2) toRow = Math.min(START_ROW, state.row + 1);
  else toX = state.x + (dir === 1 ? CELL : -CELL);
  // On land the frog stays on screen; on the river the current may carry it off.
  if (!isRiver(state.row)) toX = clamp(toX, CELL / 2, WIDTH - CELL / 2);
  if (toRow === state.row && toX === state.x) return;
  const h = state.hop;
  h.active = true; h.t = 0; h.fromX = state.x; h.fromRow = state.row; h.toX = toX; h.toRow = toRow;
}

function pressedDirection(input: RetroInput): number {
  const p = input.pressed;
  return p.up ? 0 : p.right ? 1 : p.down ? 2 : p.left ? 3 : -1;
}

/** Arrive in a bay: fill it (with any fly bonus), or die against the hedge or a full bay. */
function reachHome(state: HopperState, random: () => number): void {
  const bay = BAY_X.findIndex((bx) => Math.abs(state.x - bx) <= BAY_CATCH);
  if (bay < 0 || state.bays[bay]) {
    die(state, "splat", random);
    return;
  }
  state.bays[bay] = 1;
  let points = BAY_POINTS + 10 * Math.floor(state.timeLeft);
  if (state.flyBay === bay) {
    points += FLY_BONUS;
    state.flyBay = -1;
    state.flyTimer = 6 + random() * 6;
  }
  state.score += points;
  popup(state, BAY_X[bay], HOME_ROW * CELL + 2, points);
  emitBurst(state.particles, BAY_X[bay], HOME_ROW * CELL + 8, 12, 40, "#ffe66b", random, 0.6);
  if (state.bays.every((b) => b === 1)) {
    state.score += LEVEL_POINTS;
    state.phase = "levelup";
    state.timer = LEVEL_S;
    state.hop.active = false;
    state.flyBay = -1;
  } else {
    respawn(state);
  }
}

/** The frog sits on `row` at `x`: is it alive? Returns false after killing it. */
function survives(state: HopperState, random: () => number): boolean {
  if (isRoad(state.row) && vehicleAt(state, state.row, state.x)) {
    die(state, "splat", random);
    return false;
  }
  if (isRiver(state.row)) {
    if (state.x < 2 || state.x > WIDTH - 2) {
      die(state, "splash", random);
      return false;
    }
    if (!platformAt(state, state.row, state.x)) {
      die(state, "splash", random);
      return false;
    }
  }
  return true;
}

function land(state: HopperState, random: () => number): void {
  const h = state.hop;
  h.active = false;
  state.x = h.toX;
  state.row = h.toRow;
  if (state.row < state.bestRow) {
    state.score += 10 * (state.bestRow - state.row);
    state.bestRow = state.row;
  }
  if (state.row === HOME_ROW) {
    reachHome(state, random);
    return;
  }
  if (!survives(state, random)) return;
  if (state.queued >= 0) {
    const dir = state.queued as Direction;
    state.queued = -1;
    startHop(state, dir);
  }
}

function laneSpeed(lane: number, level: number): number {
  return LANES[lane].speed * levelFactor(level);
}

function step(state: HopperState, input: RetroInput, dt: number, random: () => number): void {
  stepParticles(state.particles, dt, 30);
  if (state.phase === "over") return;
  state.elapsed += dt;
  state.diveClock += dt;
  for (let i = 0; i < LANES.length; i += 1) state.shifts[i] = mod(state.shifts[i] + laneSpeed(i, state.level) * dt, LANES[i].period);
  for (const p of state.popups) if (p.active && (p.t -= dt) <= 0) p.active = false;

  if (state.phase === "dying") {
    state.timer -= dt;
    if (state.timer <= 0) {
      if (state.lives <= 0) state.phase = "over";
      else respawn(state);
    }
    return;
  }
  if (state.phase === "levelup") {
    state.timer -= dt;
    if (state.timer <= 0) {
      state.level += 1;
      state.bays.fill(0);
      state.flyTimer = 6 + random() * 5;
      respawn(state);
    }
    return;
  }

  // The fly visits empty bays now and then.
  if (state.flyBay >= 0) {
    state.flyLeft -= dt;
    if (state.flyLeft <= 0) {
      state.flyBay = -1;
      state.flyTimer = 6 + random() * 6;
    }
  } else if ((state.flyTimer -= dt) <= 0) {
    let empty = 0;
    for (const b of state.bays) if (!b) empty += 1;
    let pick = Math.floor(random() * empty);
    for (let i = 0; i < 5 && empty > 0; i += 1) {
      if (!state.bays[i] && pick-- === 0) { state.flyBay = i; state.flyLeft = 4.5; }
    }
    state.flyTimer = 6 + random() * 6;
  }

  state.timeLeft -= dt;
  if (state.timeLeft <= 0) {
    state.timeLeft = 0;
    die(state, "time", random);
    return;
  }

  const dir = pressedDirection(input);
  const h = state.hop;
  if (h.active) {
    if (dir >= 0) state.queued = dir;
    // A hop that starts on the river keeps drifting with what it left.
    if (isRiver(h.fromRow)) {
      const drift = laneSpeed(LANE_OF_ROW[h.fromRow], state.level) * dt;
      h.fromX += drift;
      h.toX += drift;
    }
    h.t += dt;
    if (h.t >= HOP_S) land(state, random);
    return;
  }
  if (dir >= 0) {
    startHop(state, dir as Direction);
    if (h.active) return;
  }
  // Sitting still: the river carries the frog; traffic or water may end it.
  if (isRiver(state.row)) state.x += laneSpeed(LANE_OF_ROW[state.row], state.level) * dt;
  survives(state, random);
}

function status(state: HopperState): RetroStatus {
  return { score: state.score, lives: state.lives, level: state.level, over: state.phase === "over" };
}

// ---- Drawing --------------------------------------------------------------

/** Rotate a square bitmap a quarter turn clockwise `turns` times. */
function rotateRows(rows: readonly string[], turns: number): string[] {
  let out = rows.slice();
  for (let t = 0; t < turns; t += 1) {
    const n = out.length;
    const next: string[] = [];
    for (let y = 0; y < n; y += 1) {
      let line = "";
      for (let x = 0; x < n; x += 1) line += out[n - 1 - x][y];
      next.push(line);
    }
    out = next;
  }
  return out;
}

const FROG_SIT = [
  ".###....###.",
  ".#.#....#.#.",
  "..########..",
  ".##########.",
  "#.########.#",
  "#.########.#",
  "##.######.##",
  "..########..",
  ".##.####.##.",
  "##........##",
  "#..........#",
  "............",
];
const FROG_LEAP = [
  "#.###..###.#",
  "#.#.#..#.#.#",
  ".#.######.#.",
  "..########..",
  "..########..",
  "...######...",
  "...######...",
  "..########..",
  "..##.##.##..",
  ".##......##.",
  ".#........#.",
  "#..........#",
];
/** [direction][0 sitting, 1 leaping]. */
const FROG: readonly (readonly [PixelSprite, PixelSprite])[] = [0, 1, 2, 3].map(
  (turns) => [sprite(rotateRows(FROG_SIT, turns)), sprite(rotateRows(FROG_LEAP, turns))] as const,
);
const SPLAT = sprite(["#...#..#...#", ".#..####..#.", "..########..", ".##.####.##.", "#.########.#", "..##.##.##..", ".#..#..#..#.", "#..........#"]);
const FLY = sprite([".#.#.", "#####", ".###.", "..#.."]);
const CAR = sprite(["..########......", ".##########.....", "################", "################", ".##########.....", "..########......"]);
const RACER = sprite(["....######......", "..##########....", "################", "################", "..##########....", "....######......"]);
const DOZER = sprite(["#..#########....", "#.###########...", "#.############..", "#.############..", "#.###########...", "#..#########...."]);
const LIFE_ICON = sprite([".#..#.", "######", "######", "#....#"]);

const BANK_ROWS = [7, START_ROW] as const;
const LANE_COLOR: Partial<Record<LaneKind, string>> = { car: "#4fc3ff", racer: "#ff5fb8", dozer: "#ffcc33" };

/** Lane scroll used for drawing: on the attract screen the world drifts on its own. */
function drawShift(state: HopperState, lane: number, attract: boolean, time: number): number {
  return attract ? state.shifts[lane] + LANES[lane].speed * time : state.shifts[lane];
}

function drawVehicle(ctx: CanvasRenderingContext2D, kind: LaneKind, x: number, y: number, len: number, flip: boolean): void {
  if (kind === "truck") {
    ctx.fillStyle = "#d9d4c7";
    ctx.fillRect(x + (flip ? 0 : 8), y + 2, len - 8, 12);
    ctx.fillStyle = "#e0523d";
    ctx.fillRect(x + (flip ? len - 8 : 0), y + 3, 8, 10);
    ctx.fillStyle = "#0b0b12";
    ctx.fillRect(x + (flip ? len - 5 : 1), y + 4, 3, 8);
    ctx.fillRect(x + 4, y + 1, 4, 1);
    ctx.fillRect(x + len - 8, y + 1, 4, 1);
    ctx.fillRect(x + 4, y + 14, 4, 1);
    ctx.fillRect(x + len - 8, y + 14, 4, 1);
    return;
  }
  ctx.fillStyle = "#0b0b12";
  ctx.fillRect(x + 3, y + 1, 3, 14);
  ctx.fillRect(x + 10, y + 1, 3, 14);
  ctx.fillStyle = LANE_COLOR[kind] ?? "#fff";
  drawSprite(ctx, kind === "racer" ? RACER : kind === "dozer" ? DOZER : CAR, x, y + 5, 1, flip);
  ctx.fillStyle = "#fff8d0";
  ctx.fillRect(flip ? x : x + len - 1, y + 7, 1, 2);
}

function draw(ctx: CanvasRenderingContext2D, state: HopperState, info: RetroDrawInfo): void {
  const attract = info.idle && state.elapsed === 0;
  const clock = attract ? info.time : state.diveClock;
  const motion = info.reduced ? 0 : info.time;

  ctx.fillStyle = "#06070d";
  ctx.fillRect(0, 0, WIDTH, HEIGHT);

  // River with drifting ripples.
  ctx.fillStyle = "#0b2a5c";
  ctx.fillRect(0, RIVER_TOP * CELL, WIDTH, (RIVER_BOTTOM - RIVER_TOP + 1) * CELL);
  ctx.fillStyle = "#1d4f96";
  for (let row = RIVER_TOP; row <= RIVER_BOTTOM; row += 1) {
    const drift = Math.floor(mod(motion * 6 + row * 13, 32));
    for (let x = -32 + drift; x < WIDTH; x += 32) ctx.fillRect(x, row * CELL + 5 + (row % 2) * 6, 6, 1);
  }

  // Hedge and bays.
  ctx.fillStyle = "#1f5e2b";
  ctx.fillRect(0, HOME_ROW * CELL - 2, WIDTH, CELL + 2);
  ctx.fillStyle = "#2f8a3e";
  for (let x = 0; x < WIDTH; x += 4) ctx.fillRect(x, HOME_ROW * CELL - 2 + (x % 8 === 0 ? 0 : 1), 2, 1);
  for (let i = 0; i < 5; i += 1) {
    const bx = BAY_X[i] - 10;
    ctx.fillStyle = "#0b2a5c";
    ctx.fillRect(bx, HOME_ROW * CELL, 20, CELL);
    if (state.bays[i]) {
      ctx.fillStyle = "#b6f36b";
      drawSprite(ctx, FROG[2][0], BAY_X[i] - 6, HOME_ROW * CELL + 2);
    } else if (state.flyBay === i) {
      ctx.fillStyle = "#ffe66b";
      const flap = info.reduced ? 0 : Math.floor(info.time * 10) % 2;
      drawSprite(ctx, FLY, BAY_X[i] - 2, HOME_ROW * CELL + 6 - flap);
    }
  }

  // Banks.
  for (const row of BANK_ROWS) {
    ctx.fillStyle = "#3d2f6e";
    ctx.fillRect(0, row * CELL, WIDTH, CELL);
    ctx.fillStyle = "#56449a";
    for (let x = 2; x < WIDTH; x += 8) ctx.fillRect(x + (row % 2) * 4, row * CELL + 4, 2, 2);
    for (let x = 6; x < WIDTH; x += 8) ctx.fillRect(x - (row % 2) * 4, row * CELL + 11, 2, 2);
  }

  // Road with lane markings.
  ctx.fillStyle = "#14141c";
  ctx.fillRect(0, ROAD_TOP * CELL, WIDTH, (ROAD_BOTTOM - ROAD_TOP + 1) * CELL);
  ctx.fillStyle = "#4b4b5c";
  for (let row = ROAD_TOP + 1; row <= ROAD_BOTTOM; row += 1) {
    for (let x = 4; x < WIDTH; x += 16) ctx.fillRect(x, row * CELL, 8, 1);
  }

  // Lane contents.
  for (let lane = 0; lane < LANES.length; lane += 1) {
    const def = LANES[lane];
    const shift = drawShift(state, lane, attract, info.time);
    const y = def.row * CELL;
    for (let i = 0; i < def.offsets.length; i += 1) {
      const x = Math.round(itemX(lane, i, shift));
      const len = def.lens[i];
      if (x >= WIDTH || x + len <= 0) continue;
      if (def.kind === "log") {
        ctx.fillStyle = "#8a5a2b";
        ctx.fillRect(x + 1, y + 2, len - 2, 12);
        ctx.fillStyle = "#b07a40";
        ctx.fillRect(x + 1, y + 3, len - 2, 2);
        ctx.fillStyle = "#5c3a1a";
        for (let g = x + 6; g < x + len - 6; g += 11) ctx.fillRect(g, y + 8, 5, 1);
        ctx.fillRect(x + 1, y + 2, 2, 12);
        ctx.fillStyle = "#d9a86a";
        ctx.fillRect(x + len - 3, y + 3, 2, 10);
      } else if (def.kind === "turtle") {
        const dive = diveState(lane, i, clock);
        for (let t = 0; t < len; t += CELL) {
          const tx = x + t;
          if (dive === 2) {
            ctx.fillStyle = "#1d4f96";
            ctx.fillRect(tx + 4, y + 7, 8, 1);
            continue;
          }
          ctx.fillStyle = dive === 1 ? "#6e3a2c" : "#c0463a";
          ctx.fillRect(tx + 3, y + 3, 10, 10);
          ctx.fillRect(tx + 2, y + 5, 12, 6);
          ctx.fillStyle = dive === 1 ? "#4a6a3a" : "#7ed957";
          ctx.fillRect(def.speed < 0 ? tx : tx + 14, y + 6, 2, 4);
          ctx.fillStyle = "#e8b04a";
          ctx.fillRect(tx + 6, y + 6, 4, 4);
        }
      } else {
        drawVehicle(ctx, def.kind, x, y, len, def.speed < 0);
      }
    }
  }

  // The frog, or what is left of it.
  if (state.phase === "dying") {
    const cy = state.row * CELL;
    if (state.deathKind === "splash") {
      ctx.strokeStyle = "#9fd8ff";
      ctx.lineWidth = 1;
      const grow = clamp(1 - state.timer / DEATH_S, 0, 1);
      for (let ring = 0; ring < 2; ring += 1) {
        ctx.beginPath();
        ctx.arc(state.x, cy + CELL / 2, 2 + grow * 8 + ring * 4, 0, Math.PI * 2);
        ctx.stroke();
      }
    } else {
      ctx.fillStyle = "#7ed957";
      drawSprite(ctx, SPLAT, state.x - 6, cy + 4);
    }
  } else if (state.phase === "play") {
    const h = state.hop;
    let fx = state.x, fy = state.row * CELL, leap = 0;
    if (h.active) {
      const k = clamp(h.t / HOP_S, 0, 1);
      fx = h.fromX + (h.toX - h.fromX) * k;
      fy = (h.fromRow + (h.toRow - h.fromRow) * k) * CELL - (info.reduced ? 0 : Math.sin(k * Math.PI) * 3);
      leap = 1;
    }
    ctx.fillStyle = "#0a1a0a";
    ctx.fillRect(Math.round(fx) - 4, state.row * CELL + 13, 8, 1);
    ctx.fillStyle = "#7ed957";
    drawSprite(ctx, FROG[state.facing][leap], fx - 6, fy + 2);
  }
  drawParticles(ctx, state.particles);

  ctx.fillStyle = "#ffe66b";
  for (const p of state.popups) {
    if (p.active) drawNumber(ctx, p.value, p.x, p.y - (info.reduced ? 0 : (1.2 - p.t) * 6), 1, "center");
  }

  // Top: score and level. Bottom: lives and the clock.
  ctx.fillStyle = "#e9ffe0";
  drawNumber(ctx, state.score, 6, 4, 2);
  ctx.fillStyle = "#b6f36b";
  drawNumber(ctx, state.level, WIDTH - 6, 4, 2, "right");
  ctx.fillStyle = "#7ed957";
  for (let i = 0; i < Math.min(state.lives, 6); i += 1) drawSprite(ctx, LIFE_ICON, 6 + i * 9, 14 * CELL + 6);
  const frac = clamp(state.timeLeft / LIFE_TIME, 0, 1);
  const low = state.timeLeft < 8;
  ctx.fillStyle = "#1c2a1c";
  ctx.fillRect(40, 15 * CELL + 5, WIDTH - 46, 6);
  // The bar turns red when time runs low; it pulses only without reduced motion.
  ctx.globalAlpha = low && !info.reduced && state.phase === "play" ? 0.65 + 0.35 * Math.sin(info.time * 6) : 1;
  ctx.fillStyle = low ? "#ff5a4a" : "#7ed957";
  ctx.fillRect(WIDTH - 6 - Math.round((WIDTH - 46) * frac), 15 * CELL + 5, Math.round((WIDTH - 46) * frac), 6);
  ctx.globalAlpha = 1;
  ctx.fillStyle = low ? "#ff5a4a" : "#b6f36b";
  drawNumber(ctx, Math.ceil(state.timeLeft), 6, 15 * CELL + 4, 2);

  if (state.phase === "levelup") {
    ctx.fillStyle = "#ffe66b";
    drawNumber(ctx, state.level + 1, WIDTH / 2, 7 * CELL + 1, 3, "center");
  }
}

const roadHopper: RetroGame<HopperState> = {
  id: "road-hopper",
  width: WIDTH,
  height: HEIGHT,
  create,
  step,
  draw,
  status,
};

export default roadHopper;
