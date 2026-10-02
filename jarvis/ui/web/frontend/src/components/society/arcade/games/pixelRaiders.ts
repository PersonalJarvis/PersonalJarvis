/**
 * "Pixel Raiders": a formation of pixel aliens marches sideways, drops a row
 * at each edge and speeds up as it thins out. They shoot back (some shots
 * aimed at the cannon, some from a random column); the cannon hides behind
 * four bunkers that crumble cell by cell. A bonus saucer crosses the top now
 * and then. Clearing the formation brings the next wave, starting lower and
 * marching faster; the formation reaching the cannon ends the run.
 */
import type { RetroDrawInfo, RetroGame, RetroInput, RetroStatus } from "../retroGame";
import {
  clamp, drawNumber, drawParticles, drawSprite, drawStars, emitBurst, makeParticles, sprite, starField, stepParticles,
  type Particle, type PixelSprite,
} from "./games3Kit";

export const WIDTH = 224;
export const HEIGHT = 256;

export const COLS = 11;
export const ROWS = 5;
export const CELL_W = 16;
export const CELL_H = 16;
const ALIEN_H = 8;
/** Pixels the formation moves per march step, and drops at an edge. */
export const MARCH_X = 2;
export const DROP_Y = 8;
/** The formation turns when its outer column would come closer than this to a side wall. */
const EDGE_MARGIN = 6;

/** Which alien kind each row holds (top to bottom), their widths and points. */
const ROW_KIND = [0, 1, 1, 2, 2] as const;
const KIND_W = [8, 10, 12] as const;
export const KIND_POINTS = [30, 20, 10] as const;
const KIND_COLOR = ["#ff7ae0", "#7ae8ff", "#9dff7a"] as const;

export const PLAYER_Y = 216;
export const PLAYER_W = 13;
const PLAYER_H = 8;
const PLAYER_SPEED = 80;
const PLAYER_START_X = Math.round((WIDTH - PLAYER_W) / 2);
export const MAX_SHOTS = 2;
const SHOT_SPEED = 260;
const SHOT_COOLDOWN = 0.32;
const GROUND_Y = 230;

export const MAX_BOMBS = 6;

export const BUNKER_COUNT = 4;
export const BUNKER_COLS = 11;
export const BUNKER_ROWS = 8;
/** Bunker cells are 2 × 2 pixels. */
export const BUNKER_CELL = 2;
export const BUNKER_Y = 184;
export const BUNKER_X: readonly number[] = [0, 1, 2, 3].map((i) => Math.round((WIDTH * (i + 1)) / 5 - (BUNKER_COLS * BUNKER_CELL) / 2));
const BUNKER_SHAPE = [
  "..#######..",
  ".#########.",
  "###########",
  "###########",
  "###########",
  "###########",
  "###.....###",
  "##.......##",
];

const SAUCER_W = 16;
const SAUCER_Y = 26;
const SAUCER_SPEED = 48;
const SAUCER_POINTS = [50, 100, 100, 150, 150, 300] as const;

const DEATH_S = 1.4;
const CLEAR_S = 1.6;
const EXTRA_LIFE_EVERY = 2000;
const MAX_LIVES = 5;

/** Two animation frames per alien kind: our own designs, a jelly, a horned beetle and a crested trilobite. */
const ALIEN_SPRITES: readonly (readonly [PixelSprite, PixelSprite])[] = [
  [
    sprite(["..####..", ".######.", "##.##.##", "########", ".#.##.#.", "#..##..#", ".#....#.", "..#..#.."]),
    sprite(["..####..", ".######.", "##.##.##", "########", ".#.##.#.", ".#.##.#.", "#..##..#", "#......#"]),
  ],
  [
    sprite(["#........#", ".#..##..#.", "..######..", ".##.##.##.", "##########", ".#.####.#.", "#.#....#.#", ".#......#."]),
    sprite(["..#....#..", "..#.##.#..", "..######..", ".##.##.##.", "##########", "#.######.#", "..#....#..", ".#.#..#.#."]),
  ],
  [
    sprite([".....##.....", "...######...", ".##########.", "##.##..##.##", "############", ".#.#.##.#.#.", "#.#......#.#", ".#........#."]),
    sprite([".....##.....", "...######...", ".##########.", "##.##..##.##", "############", "..#.#..#.#..", ".#.#....#.#.", "#.#......#.#"]),
  ],
];
const BURST_SPRITE = sprite(["#..#..#.#..", ".#..#.#..#.", "..#.....#..", "##.......##", "..#.....#..", ".#..#.#..#.", "#..#...#..#"]);
const PLAYER_SPRITE = sprite([
  "......#......",
  ".....###.....",
  "....#####....",
  ".###########.",
  "#############",
  "#############",
  "#.#.#.#.#.#.#",
  ".#.#.#.#.#.#.",
]);
const WRECK_SPRITES = [
  sprite(["..#.....#....", "....#.#...#..", "#..#####.....", "..#######.#..", ".##.###.###..", "#############", "#.#.#.#.#.#.#", ".#.#.#.#.#.#."]),
  sprite(["#.....#....#.", "...#.....#...", "..#.##.#.....", "#.##.###.##..", ".###.###.###.", "#############", ".#.#.#.#.#.#.", "#.#.#.#.#.#.#"]),
];
const SAUCER_SPRITE = sprite([
  ".....######.....",
  "...##########...",
  "..############..",
  ".##.##.##.##.##.",
  "################",
  "..###..##..###..",
  "...#........#...",
]);
const LIFE_SPRITE = sprite(["...#...", "..###..", "#######", "#######"]);

export type RaidersPhase = "play" | "dying" | "clear" | "over";

export interface Shot { active: boolean; x: number; y: number }
/** An alien shot. `kind` 0 straight, 1 zigzag (aimed), 2 fast dart. */
export interface Bomb { active: boolean; x: number; y: number; kind: number; speed: number }
interface Burst { active: boolean; x: number; y: number; t: number; color: string }
interface Popup { active: boolean; x: number; y: number; t: number; value: number }

export interface RaidersState {
  phase: RaidersPhase;
  /** Seconds left in a dying / clear phase. */
  timer: number;
  /** Total seconds simulated; zero means the run has not started (attract screen). */
  elapsed: number;
  score: number;
  lives: number;
  wave: number;
  nextLifeAt: number;
  px: number;
  cooldown: number;
  shots: Shot[];
  bombs: Bomb[];
  /** One flag per alien, row-major (ROWS × COLS). */
  alive: Uint8Array;
  aliveCount: number;
  /** Top-left of the formation's grid of cells. */
  fx: number;
  fy: number;
  dir: 1 | -1;
  stepTimer: number;
  frame: 0 | 1;
  fireTimer: number;
  /** One flag per bunker cell, bunker-major (BUNKER_COUNT × BUNKER_ROWS × BUNKER_COLS). */
  bunkers: Uint8Array;
  saucer: { active: boolean; x: number; dir: 1 | -1; points: number };
  saucerTimer: number;
  bursts: Burst[];
  popups: Popup[];
  particles: Particle[];
  shake: number;
}

/** Seconds between march steps: slow with a full formation, every frame with one alien left, faster each wave. */
export function stepDelay(alive: number, wave: number): number {
  const fill = clamp((alive - 1) / (ROWS * COLS - 1), 0, 1);
  return (0.012 + 0.45 * fill) * Math.max(0.55, 1 - 0.07 * (wave - 1));
}

/** The formation's top edge at the start of a wave: each wave starts a row lower, up to a limit. */
export function waveStartY(wave: number): number {
  return 48 + Math.min(wave - 1, 7) * 8;
}

function maxBombs(wave: number): number {
  return Math.min(MAX_BOMBS, 2 + Math.floor(wave / 2));
}

function resetBunkers(bunkers: Uint8Array): void {
  for (let b = 0; b < BUNKER_COUNT; b += 1) {
    for (let r = 0; r < BUNKER_ROWS; r += 1) {
      for (let c = 0; c < BUNKER_COLS; c += 1) {
        bunkers[(b * BUNKER_ROWS + r) * BUNKER_COLS + c] = BUNKER_SHAPE[r][c] === "#" ? 1 : 0;
      }
    }
  }
}

/** Solid cells left in one bunker (tests and balance). */
export function bunkerCells(state: RaidersState, bunker: number): number {
  let n = 0;
  const base = bunker * BUNKER_ROWS * BUNKER_COLS;
  for (let i = 0; i < BUNKER_ROWS * BUNKER_COLS; i += 1) n += state.bunkers[base + i];
  return n;
}

function startWave(state: RaidersState, random: () => number): void {
  state.alive.fill(1);
  state.aliveCount = ROWS * COLS;
  state.fx = Math.round((WIDTH - COLS * CELL_W) / 2);
  state.fy = waveStartY(state.wave);
  state.dir = 1;
  state.frame = 0;
  state.stepTimer = stepDelay(state.aliveCount, state.wave);
  state.fireTimer = 1.2 + random();
  for (const s of state.shots) s.active = false;
  for (const b of state.bombs) b.active = false;
  resetBunkers(state.bunkers);
  state.saucer.active = false;
  state.saucerTimer = 14 + random() * 10;
}

function create(random: () => number): RaidersState {
  const state: RaidersState = {
    phase: "play", timer: 0, elapsed: 0, score: 0, lives: 3, wave: 1, nextLifeAt: EXTRA_LIFE_EVERY,
    px: PLAYER_START_X, cooldown: 0,
    shots: Array.from({ length: MAX_SHOTS }, () => ({ active: false, x: 0, y: 0 })),
    bombs: Array.from({ length: MAX_BOMBS }, () => ({ active: false, x: 0, y: 0, kind: 0, speed: 0 })),
    alive: new Uint8Array(ROWS * COLS), aliveCount: 0,
    fx: 0, fy: 0, dir: 1, stepTimer: 0, frame: 0, fireTimer: 0,
    bunkers: new Uint8Array(BUNKER_COUNT * BUNKER_ROWS * BUNKER_COLS),
    saucer: { active: false, x: 0, dir: 1, points: 0 }, saucerTimer: 0,
    bursts: Array.from({ length: 8 }, () => ({ active: false, x: 0, y: 0, t: 0, color: "#fff" })),
    popups: Array.from({ length: 3 }, () => ({ active: false, x: 0, y: 0, t: 0, value: 0 })),
    particles: makeParticles(120),
    shake: 0,
  };
  startWave(state, random);
  return state;
}

/** Left pixel of the alien at (row, col). */
export function alienX(state: RaidersState, row: number, col: number): number {
  return state.fx + col * CELL_W + (CELL_W - KIND_W[ROW_KIND[row]]) / 2;
}

export function alienY(state: RaidersState, row: number): number {
  return state.fy + row * CELL_H;
}

function addScore(state: RaidersState, points: number): void {
  state.score += points;
  if (state.score >= state.nextLifeAt) {
    state.nextLifeAt += EXTRA_LIFE_EVERY;
    state.lives = Math.min(MAX_LIVES, state.lives + 1);
  }
}

function spawnBurst(state: RaidersState, x: number, y: number, color: string): void {
  const slot = state.bursts.find((b) => !b.active) ?? state.bursts[0];
  slot.active = true; slot.x = x; slot.y = y; slot.t = 0.2; slot.color = color;
}

/** Erode a bunker around the cell that was hit; `down` is the shot's direction of travel. */
function erode(state: RaidersState, bunker: number, cx: number, cy: number, down: boolean, random: () => number): void {
  const base = bunker * BUNKER_ROWS * BUNKER_COLS;
  for (let dy = -1; dy <= 1; dy += 1) {
    for (let dx = -1; dx <= 1; dx += 1) {
      const x = cx + dx, y = cy + dy;
      if (x < 0 || y < 0 || x >= BUNKER_COLS || y >= BUNKER_ROWS) continue;
      if ((dx === 0 && dy === 0) || random() < 0.4) state.bunkers[base + y * BUNKER_COLS + x] = 0;
    }
  }
  // Bites go a little deeper in the direction the shot was travelling.
  const deeper = cy + (down ? 2 : -2);
  if (deeper >= 0 && deeper < BUNKER_ROWS && random() < 0.5) state.bunkers[base + deeper * BUNKER_COLS + cx] = 0;
  emitBurst(state.particles, BUNKER_X[bunker] + cx * BUNKER_CELL, BUNKER_Y + cy * BUNKER_CELL, 4, 30, "#4dff88", random, 0.3);
}

/** If (x, y) is inside a solid bunker cell, chip it and report the hit. */
function hitBunker(state: RaidersState, x: number, y: number, down: boolean, random: () => number): boolean {
  if (y < BUNKER_Y || y >= BUNKER_Y + BUNKER_ROWS * BUNKER_CELL) return false;
  for (let b = 0; b < BUNKER_COUNT; b += 1) {
    const bx = BUNKER_X[b];
    if (x < bx || x >= bx + BUNKER_COLS * BUNKER_CELL) continue;
    const cx = Math.floor((x - bx) / BUNKER_CELL), cy = Math.floor((y - BUNKER_Y) / BUNKER_CELL);
    if (state.bunkers[(b * BUNKER_ROWS + cy) * BUNKER_COLS + cx]) {
      erode(state, b, cx, cy, down, random);
      return true;
    }
    return false;
  }
  return false;
}

function killAlien(state: RaidersState, row: number, col: number, random: () => number): void {
  state.alive[row * COLS + col] = 0;
  state.aliveCount -= 1;
  const kind = ROW_KIND[row];
  addScore(state, KIND_POINTS[kind]);
  const x = alienX(state, row, col), y = alienY(state, row);
  spawnBurst(state, x + KIND_W[kind] / 2, y + ALIEN_H / 2, KIND_COLOR[kind]);
  emitBurst(state.particles, x + KIND_W[kind] / 2, y + ALIEN_H / 2, 10, 70, KIND_COLOR[kind], random, 0.45);
  if (state.aliveCount === 0) {
    state.phase = "clear";
    state.timer = CLEAR_S;
    for (const b of state.bombs) b.active = false;
    state.saucer.active = false;
  }
}

/** The alien hit by a point, as row * COLS + col, or -1. */
function alienAt(state: RaidersState, x: number, y: number): number {
  const col = Math.floor((x - state.fx) / CELL_W), row = Math.floor((y - state.fy) / CELL_H);
  if (col < 0 || col >= COLS || row < 0 || row >= ROWS || !state.alive[row * COLS + col]) return -1;
  const ax = alienX(state, row, col), ay = alienY(state, row);
  return x >= ax && x < ax + KIND_W[ROW_KIND[row]] && y >= ay && y < ay + ALIEN_H ? row * COLS + col : -1;
}

function killPlayer(state: RaidersState, random: () => number): void {
  state.phase = "dying";
  state.timer = DEATH_S;
  state.lives = Math.max(0, state.lives - 1);
  state.shake = 0.4;
  for (const b of state.bombs) b.active = false;
  for (const s of state.shots) s.active = false;
  emitBurst(state.particles, state.px + PLAYER_W / 2, PLAYER_Y + 4, 28, 90, "#63f3ff", random, 0.9);
  emitBurst(state.particles, state.px + PLAYER_W / 2, PLAYER_Y + 4, 14, 60, "#fff6a8", random, 0.6);
}

/** Bounds of the living formation: [minCol, maxCol, maxRow], or null when it is empty. */
function formationBounds(state: RaidersState, out: number[]): boolean {
  let minCol = COLS, maxCol = -1, maxRow = -1;
  for (let r = 0; r < ROWS; r += 1) {
    for (let c = 0; c < COLS; c += 1) {
      if (!state.alive[r * COLS + c]) continue;
      if (c < minCol) minCol = c;
      if (c > maxCol) maxCol = c;
      if (r > maxRow) maxRow = r;
    }
  }
  out[0] = minCol; out[1] = maxCol; out[2] = maxRow;
  return maxCol >= 0;
}
const bounds = [0, 0, 0];

/** One march step: sideways, or down and turn around at a wall. */
export function march(state: RaidersState, random: () => number): void {
  if (!formationBounds(state, bounds)) return;
  const [minCol, maxCol, maxRow] = bounds;
  const left = state.fx + minCol * CELL_W + 1;
  const right = state.fx + (maxCol + 1) * CELL_W - 1;
  const atWall = state.dir > 0 ? right + MARCH_X > WIDTH - EDGE_MARGIN : left - MARCH_X < EDGE_MARGIN;
  if (atWall) {
    state.fy += DROP_Y;
    state.dir = state.dir > 0 ? -1 : 1;
  } else {
    state.fx += MARCH_X * state.dir;
  }
  state.frame = state.frame ? 0 : 1;
  const bottom = state.fy + maxRow * CELL_H + ALIEN_H;
  // Aliens grind through any bunker they march into.
  if (bottom > BUNKER_Y) {
    for (let r = 0; r <= maxRow; r += 1) {
      const ay = alienY(state, r);
      if (ay + ALIEN_H <= BUNKER_Y) continue;
      for (let c = 0; c < COLS; c += 1) {
        if (!state.alive[r * COLS + c]) continue;
        const ax = alienX(state, r, c);
        for (let y = ay; y < ay + ALIEN_H; y += BUNKER_CELL) {
          for (let x = ax; x < ax + KIND_W[ROW_KIND[r]]; x += BUNKER_CELL) clearBunkerAt(state, x, y);
        }
      }
    }
  }
  if (bottom >= PLAYER_Y && state.phase === "play") {
    // The formation has landed: the run is over no matter how many lives are left.
    killPlayer(state, random);
    state.lives = 0;
  }
}

function clearBunkerAt(state: RaidersState, x: number, y: number): void {
  if (y < BUNKER_Y || y >= BUNKER_Y + BUNKER_ROWS * BUNKER_CELL) return;
  for (let b = 0; b < BUNKER_COUNT; b += 1) {
    const bx = BUNKER_X[b];
    if (x < bx || x >= bx + BUNKER_COLS * BUNKER_CELL) continue;
    state.bunkers[(b * BUNKER_ROWS + Math.floor((y - BUNKER_Y) / BUNKER_CELL)) * BUNKER_COLS + Math.floor((x - bx) / BUNKER_CELL)] = 0;
  }
}

/** Fire an alien shot: aimed from the column above the cannon, or from a random column. */
function alienFire(state: RaidersState, random: () => number): void {
  let active = 0;
  for (const b of state.bombs) if (b.active) active += 1;
  if (active >= maxBombs(state.wave)) return;
  const aimed = random() < 0.45;
  let col = -1;
  if (aimed) {
    let best = Infinity;
    const target = state.px + PLAYER_W / 2;
    for (let c = 0; c < COLS; c += 1) {
      if (!columnAlive(state, c)) continue;
      const d = Math.abs(state.fx + c * CELL_W + CELL_W / 2 - target);
      if (d < best) { best = d; col = c; }
    }
  } else {
    // Pick the n-th living column.
    let living = 0;
    for (let c = 0; c < COLS; c += 1) if (columnAlive(state, c)) living += 1;
    let pick = Math.floor(random() * living);
    for (let c = 0; c < COLS && col < 0; c += 1) if (columnAlive(state, c) && pick-- === 0) col = c;
  }
  if (col < 0) return;
  let row = ROWS - 1;
  while (row >= 0 && !state.alive[row * COLS + col]) row -= 1;
  const bomb = state.bombs.find((b) => !b.active);
  if (!bomb || row < 0) return;
  bomb.active = true;
  bomb.x = Math.round(state.fx + col * CELL_W + CELL_W / 2);
  bomb.y = alienY(state, row) + ALIEN_H;
  bomb.kind = aimed ? 1 : random() < 0.3 ? 2 : 0;
  bomb.speed = 70 + 6 * Math.min(state.wave, 8) + (bomb.kind === 2 ? 30 : 0);
}

function columnAlive(state: RaidersState, col: number): boolean {
  for (let r = 0; r < ROWS; r += 1) if (state.alive[r * COLS + col]) return true;
  return false;
}

function stepShots(state: RaidersState, dt: number, random: () => number): void {
  for (const shot of state.shots) {
    if (!shot.active) continue;
    // Move in 2-pixel steps so a fast shot never skips over a bunker cell or an alien.
    const steps = Math.max(1, Math.ceil((SHOT_SPEED * dt) / 2));
    const stepLen = (SHOT_SPEED * dt) / steps;
    for (let i = 0; i < steps && shot.active; i += 1) {
      shot.y -= stepLen;
      if (shot.y < 12) { shot.active = false; break; }
      if (hitBunker(state, shot.x, shot.y, false, random)) { shot.active = false; break; }
      const hit = alienAt(state, shot.x, shot.y);
      if (hit >= 0) {
        shot.active = false;
        killAlien(state, Math.floor(hit / COLS), hit % COLS, random);
        break;
      }
      const s = state.saucer;
      if (s.active && shot.x >= s.x && shot.x < s.x + SAUCER_W && shot.y >= SAUCER_Y && shot.y < SAUCER_Y + 7) {
        shot.active = false;
        s.active = false;
        addScore(state, s.points);
        const popup = state.popups.find((p) => !p.active) ?? state.popups[0];
        popup.active = true; popup.x = s.x + SAUCER_W / 2; popup.y = SAUCER_Y; popup.t = 1.2; popup.value = s.points;
        emitBurst(state.particles, s.x + SAUCER_W / 2, SAUCER_Y + 3, 18, 80, "#ff4f6d", random, 0.6);
        break;
      }
      for (const b of state.bombs) {
        if (b.active && Math.abs(b.x - shot.x) <= 1.5 && shot.y >= b.y - 1 && shot.y <= b.y + 7) {
          b.active = false;
          shot.active = false;
          emitBurst(state.particles, shot.x, shot.y, 6, 40, "#ffffff", random, 0.3);
          break;
        }
      }
    }
  }
}

function stepBombs(state: RaidersState, dt: number, random: () => number): void {
  for (const b of state.bombs) {
    if (!b.active) continue;
    const steps = Math.max(1, Math.ceil((b.speed * dt) / 2));
    const stepLen = (b.speed * dt) / steps;
    for (let i = 0; i < steps && b.active; i += 1) {
      b.y += stepLen;
      const tip = b.y + 6;
      if (tip >= GROUND_Y) {
        b.active = false;
        emitBurst(state.particles, b.x, GROUND_Y - 1, 4, 25, "#c58bff", random, 0.3);
        break;
      }
      if (hitBunker(state, b.x, tip, true, random)) { b.active = false; break; }
      if (state.phase === "play" && b.x >= state.px && b.x < state.px + PLAYER_W && tip >= PLAYER_Y + 2 && b.y < PLAYER_Y + PLAYER_H) {
        b.active = false;
        killPlayer(state, random);
        return;
      }
    }
  }
}

function step(state: RaidersState, input: RetroInput, dt: number, random: () => number): void {
  if (state.phase === "over") {
    stepParticles(state.particles, dt, 40);
    return;
  }
  state.elapsed += dt;
  state.shake = Math.max(0, state.shake - dt);
  stepParticles(state.particles, dt, 40);
  for (const b of state.bursts) if (b.active && (b.t -= dt) <= 0) b.active = false;
  for (const p of state.popups) if (p.active && (p.t -= dt) <= 0) p.active = false;

  if (state.phase === "dying") {
    state.timer -= dt;
    if (state.timer <= 0) {
      if (state.lives <= 0) {
        state.phase = "over";
      } else {
        state.phase = "play";
        state.px = PLAYER_START_X;
        state.cooldown = 0.4;
        state.fireTimer = Math.max(state.fireTimer, 1);
      }
    }
    return;
  }
  if (state.phase === "clear") {
    stepShots(state, dt, random);
    state.timer -= dt;
    if (state.timer <= 0) {
      state.wave += 1;
      state.phase = "play";
      startWave(state, random);
    }
    return;
  }

  // The cannon.
  const move = (input.right ? 1 : 0) - (input.left ? 1 : 0);
  state.px = clamp(state.px + move * PLAYER_SPEED * dt, 4, WIDTH - 4 - PLAYER_W);
  state.cooldown -= dt;
  // A tap shorter than one frame still fires: the press edge counts like a held button.
  if ((input.a || input.pressed.a) && state.cooldown <= 0) {
    const shot = state.shots.find((s) => !s.active);
    if (shot) {
      shot.active = true;
      shot.x = Math.round(state.px) + 6;
      shot.y = PLAYER_Y - 1;
      state.cooldown = SHOT_COOLDOWN;
    }
  }
  stepShots(state, dt, random);
  if (state.phase !== "play") return;

  // The formation.
  state.stepTimer -= dt;
  if (state.stepTimer <= 0) {
    march(state, random);
    state.stepTimer = stepDelay(state.aliveCount, state.wave);
    if (state.phase !== "play") return;
  }
  state.fireTimer -= dt;
  if (state.fireTimer <= 0) {
    alienFire(state, random);
    state.fireTimer = (0.45 + random() * 0.9) * Math.max(0.45, 1 - 0.08 * (state.wave - 1));
  }
  stepBombs(state, dt, random);
  if (state.phase !== "play") return;

  // The bonus saucer.
  const s = state.saucer;
  if (s.active) {
    s.x += s.dir * SAUCER_SPEED * dt;
    if (s.x < -SAUCER_W - 2 || s.x > WIDTH + 2) s.active = false;
  } else {
    state.saucerTimer -= dt;
    if (state.saucerTimer <= 0 && state.aliveCount >= 8) {
      s.active = true;
      s.dir = random() < 0.5 ? 1 : -1;
      s.x = s.dir > 0 ? -SAUCER_W : WIDTH;
      s.points = SAUCER_POINTS[Math.floor(random() * SAUCER_POINTS.length)];
      state.saucerTimer = 16 + random() * 14;
    }
  }
}

function status(state: RaidersState): RetroStatus {
  return { score: state.score, lives: state.lives, level: state.wave, over: state.phase === "over" };
}

const STARS = starField(40, WIDTH, 200, 7);

function draw(ctx: CanvasRenderingContext2D, state: RaidersState, info: RetroDrawInfo): void {
  ctx.fillStyle = "#05030f";
  ctx.fillRect(0, 0, WIDTH, HEIGHT);
  drawStars(ctx, STARS, info.time, info.reduced);

  ctx.save();
  if (state.shake > 0 && !info.reduced) {
    ctx.translate(Math.round(Math.sin(info.time * 83) * state.shake * 5), Math.round(Math.cos(info.time * 71) * state.shake * 3));
  }

  // Before the first step the formation shuffles gently on the title screen.
  const frame = info.idle && state.elapsed === 0 ? Math.floor(info.time * 1.6) % 2 : state.frame;
  for (let r = 0; r < ROWS; r += 1) {
    const kind = ROW_KIND[r];
    ctx.fillStyle = KIND_COLOR[kind];
    const spr = ALIEN_SPRITES[kind][frame];
    for (let c = 0; c < COLS; c += 1) {
      if (state.alive[r * COLS + c]) drawSprite(ctx, spr, alienX(state, r, c), alienY(state, r));
    }
  }
  for (const b of state.bursts) {
    if (!b.active) continue;
    ctx.fillStyle = b.color;
    drawSprite(ctx, BURST_SPRITE, b.x - 5, b.y - 3);
  }

  const s = state.saucer;
  if (s.active) {
    ctx.fillStyle = "#ff4f6d";
    drawSprite(ctx, SAUCER_SPRITE, s.x, SAUCER_Y);
    // Running lights travel along the rim (held still under reduced motion).
    ctx.fillStyle = "#fff3a8";
    const lit = info.reduced ? 0 : Math.floor(info.time * 8) % 5;
    for (let i = 0; i < 5; i += 1) if (i === lit || info.reduced) ctx.fillRect(Math.round(s.x) + 1 + i * 3, SAUCER_Y + 3, 1, 1);
  }
  ctx.fillStyle = "#ffd35a";
  for (const p of state.popups) {
    if (p.active) drawNumber(ctx, p.value, p.x, p.y + (info.reduced ? 0 : -(1.2 - p.t) * 6), 1, "center");
  }

  ctx.fillStyle = "#4dff88";
  for (let b = 0; b < BUNKER_COUNT; b += 1) {
    for (let r = 0; r < BUNKER_ROWS; r += 1) {
      for (let c = 0; c < BUNKER_COLS; c += 1) {
        if (state.bunkers[(b * BUNKER_ROWS + r) * BUNKER_COLS + c]) {
          ctx.fillRect(BUNKER_X[b] + c * BUNKER_CELL, BUNKER_Y + r * BUNKER_CELL, BUNKER_CELL, BUNKER_CELL);
        }
      }
    }
  }

  if (state.phase === "dying") {
    // The wreck smoulders between two frames; under reduced motion it holds still.
    ctx.fillStyle = "#63f3ff";
    drawSprite(ctx, WRECK_SPRITES[info.reduced ? 0 : Math.floor(info.time * 6) % 2], state.px, PLAYER_Y);
  } else if (state.phase !== "over") {
    ctx.fillStyle = "#63f3ff";
    drawSprite(ctx, PLAYER_SPRITE, state.px, PLAYER_Y);
  }

  ctx.fillStyle = "#ffffff";
  for (const shot of state.shots) if (shot.active) ctx.fillRect(shot.x, Math.round(shot.y), 1, 5);
  for (const b of state.bombs) {
    if (!b.active) continue;
    const y = Math.round(b.y);
    if (b.kind === 1) {
      ctx.fillStyle = "#ffd35a";
      const phase = Math.floor(y / 3) % 2;
      for (let i = 0; i < 7; i += 1) ctx.fillRect(b.x - 1 + ((i + phase) % 2) * 2, y + i, 1, 1);
    } else if (b.kind === 2) {
      ctx.fillStyle = "#ff7a5a";
      ctx.fillRect(b.x, y, 1, 7);
      ctx.fillRect(b.x - 1, y + 4, 3, 1);
    } else {
      ctx.fillStyle = "#f0e6ff";
      ctx.fillRect(b.x, y, 1, 6);
      ctx.fillRect(b.x - 1, y + 1 + (Math.floor(y / 4) % 2) * 3, 3, 1);
    }
  }
  drawParticles(ctx, state.particles);
  ctx.restore();

  // Ground, score, wave and spare cannons.
  ctx.fillStyle = "#5c3dff";
  ctx.fillRect(0, GROUND_Y, WIDTH, 1);
  ctx.fillStyle = "#e9e4ff";
  drawNumber(ctx, state.score, 6, 5, 2);
  ctx.fillStyle = "#c58bff";
  drawNumber(ctx, state.wave, WIDTH - 6, 5, 2, "right");
  ctx.fillStyle = "#63f3ff";
  for (let i = 0; i < Math.min(state.lives, MAX_LIVES); i += 1) drawSprite(ctx, LIFE_SPRITE, 6 + i * 10, GROUND_Y + 10);

  if (state.phase === "clear") {
    // The coming wave's number, large, while the field resets.
    ctx.fillStyle = "#c58bff";
    drawNumber(ctx, state.wave + 1, WIDTH / 2, 110, 5, "center");
  }
}

const pixelRaiders: RetroGame<RaidersState> = {
  id: "pixel-raiders",
  width: WIDTH,
  height: HEIGHT,
  create,
  step,
  draw,
  status,
};

export default pixelRaiders;
