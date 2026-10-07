/**
 * "Neon Snake": steer a glowing snake around a neon grid, eat to grow, and
 * never run into a wall, an obstacle or yourself.
 *
 * The snake moves one cell per tick; the tick gets shorter as the snake gets
 * longer and the level rises. Turns are buffered (up to two), so a quick
 * up-then-left between two ticks is not lost, and a turn straight back into
 * the neck is ignored. Every few foods a timed golden bonus fruit appears, and
 * every FOODS_PER_LEVEL foods the level rises and a few obstacle blocks drop
 * in — never on the snake and never in the cells just ahead of its head.
 */
import type { RetroGame, RetroInput } from "../retroGame";
import { clamp, drawScanlines, drawSparks, emitSparks, sparkPool, stepSparks, type SparkPool } from "./games1Kit";

export const COLS = 32;
export const ROWS = 24;
export const CELL = 10;
const WIDTH = COLS * CELL;
const HEIGHT = ROWS * CELL;

/** Directions: 0 right, 1 down, 2 left, 3 up; opposite = (d + 2) % 4. */
export const RIGHT = 0;
export const DOWN = 1;
export const LEFT = 2;
export const UP = 3;
const DIR_X = [1, 0, -1, 0];
const DIR_Y = [0, 1, 0, -1];

export const START_LENGTH = 4;
/** How many turns can wait for the next tick. */
export const MAX_QUEUED_TURNS = 2;
export const FOODS_PER_LEVEL = 5;
/** A bonus fruit appears after every BONUS_EVERY-th food. */
export const BONUS_EVERY = 4;
/** Seconds a bonus fruit stays before it fades. */
export const BONUS_S = 6;
/** Obstacles never land this close (Manhattan cells) to the head, nor in the line ahead of it. */
const SAFE_RADIUS = 4;
const SAFE_AHEAD = 8;
const MAX_OBSTACLES = 48;

export const EMPTY = 0;
export const SNAKE = 1;
export const WALL = 2;

export interface SnakeState {
  /** Cell indices (y * COLS + x), head first. */
  body: number[];
  /** What occupies every cell (EMPTY / SNAKE / WALL), for O(1) collisions. */
  grid: Uint8Array;
  dir: number;
  /** Turns waiting for the next tick, oldest first. */
  queue: number[];
  /** Cells still to grow: the tail stays put for that many ticks. */
  grow: number;
  /** Seconds since the last tick. */
  clock: number;
  food: number;
  /** The bonus fruit's cell, or -1. */
  bonus: number;
  bonusTime: number;
  obstacles: number[];
  eaten: number;
  score: number;
  level: number;
  over: boolean;
  /** The board is full: nowhere left to put food. */
  won: boolean;
  /** Game time in seconds, for the fruit's pulse. */
  time: number;
  /** Screen flash 0..1 (level up, crash) and its colour; drawn only without reduced motion. */
  flash: number;
  flashColor: string;
  shake: number;
  sparks: SparkPool;
}

/** Seconds per tick: 0.13 s at the start, faster with every cell of length and every level, never under 0.05 s. */
export function moveInterval(s: SnakeState): number {
  return Math.max(0.05, 0.13 - (s.body.length - START_LENGTH) * 0.002 - (s.level - 1) * 0.004);
}

export function cellOf(x: number, y: number): number {
  return y * COLS + x;
}

function createSnake(random: () => number): SnakeState {
  const s: SnakeState = {
    body: [], grid: new Uint8Array(COLS * ROWS), dir: RIGHT, queue: [], grow: 0, clock: 0,
    food: -1, bonus: -1, bonusTime: 0, obstacles: [], eaten: 0, score: 0, level: 1,
    over: false, won: false, time: 0, flash: 0, flashColor: "#5dff9c", shake: 0, sparks: sparkPool(96),
  };
  const y = Math.floor(ROWS / 2);
  for (let i = 0; i < START_LENGTH; i += 1) {
    const c = cellOf(8 - i, y);
    s.body.push(c);
    s.grid[c] = SNAKE;
  }
  s.food = freeCell(s, random, false);
  return s;
}

/** True when the cell is empty and free of fruit; with `guard`, also clear of the snake's path. */
function isFree(s: SnakeState, cell: number, guard: boolean): boolean {
  if (s.grid[cell] !== EMPTY || cell === s.food || cell === s.bonus) return false;
  if (!guard) return true;
  const head = s.body[0];
  const hx = head % COLS, hy = Math.floor(head / COLS);
  const x = cell % COLS, y = Math.floor(cell / COLS);
  if (Math.abs(x - hx) + Math.abs(y - hy) <= SAFE_RADIUS) return false;
  // The straight line ahead of the head, for the current and every queued direction.
  for (let q = -1; q < s.queue.length; q += 1) {
    const d = q < 0 ? s.dir : s.queue[q];
    const dx = x - hx, dy = y - hy;
    for (let k = 1; k <= SAFE_AHEAD; k += 1) if (dx === DIR_X[d] * k && dy === DIR_Y[d] * k) return false;
  }
  return true;
}

/** A random free cell, or -1 when there is none. Tries random picks first, then scans. */
function freeCell(s: SnakeState, random: () => number, guard: boolean): number {
  const total = COLS * ROWS;
  for (let i = 0; i < 64; i += 1) {
    const c = Math.floor(random() * total);
    if (isFree(s, c, guard)) return c;
  }
  const start = Math.floor(random() * total);
  for (let i = 0; i < total; i += 1) {
    const c = (start + i) % total;
    if (isFree(s, c, guard)) return c;
  }
  return -1;
}

/** Queue a turn unless it repeats the last direction, reverses into the neck, or the buffer is full. */
export function queueTurn(s: SnakeState, d: number): void {
  const last = s.queue.length > 0 ? s.queue[s.queue.length - 1] : s.dir;
  if (d === last || d === (last + 2) % 4 || s.queue.length >= MAX_QUEUED_TURNS) return;
  s.queue.push(d);
}

function centre(cell: number): [number, number] {
  return [(cell % COLS) * CELL + CELL / 2, Math.floor(cell / COLS) * CELL + CELL / 2];
}

function crash(s: SnakeState, random: () => number): void {
  s.over = true;
  s.flash = 1;
  s.flashColor = "#ff3d5a";
  s.shake = 0.35;
  for (let i = 0; i < s.body.length; i += 3) {
    const cx = (s.body[i] % COLS) * CELL + CELL / 2, cy = Math.floor(s.body[i] / COLS) * CELL + CELL / 2;
    emitSparks(s.sparks, cx, cy, 3, 70, i === 0 ? "#ffffff" : "#ff5d7a", random, 0.8, 2);
  }
}

function levelUp(s: SnakeState, random: () => number): void {
  s.level += 1;
  s.flash = 0.8;
  s.flashColor = "#5dff9c";
  const add = Math.min(2 + s.level, MAX_OBSTACLES - s.obstacles.length);
  for (let i = 0; i < add; i += 1) {
    const c = freeCell(s, random, true);
    if (c < 0) break;
    s.grid[c] = WALL;
    s.obstacles.push(c);
    const x = (c % COLS) * CELL + CELL / 2, y = Math.floor(c / COLS) * CELL + CELL / 2;
    emitSparks(s.sparks, x, y, 4, 40, "#ff4fd8", random, 0.5, 2);
  }
}

function eatFood(s: SnakeState, random: () => number): void {
  const [x, y] = centre(s.food);
  emitSparks(s.sparks, x, y, 10, 90, "#ff4d6d", random, 0.5, 2);
  s.score += 10 * s.level;
  s.grow += 1;
  s.eaten += 1;
  s.food = -1;
  if (s.eaten % FOODS_PER_LEVEL === 0) levelUp(s, random);
  s.food = freeCell(s, random, false);
  if (s.food < 0) {
    s.over = true;
    s.won = true;
    return;
  }
  if (s.eaten % BONUS_EVERY === 0 && s.bonus < 0) {
    s.bonus = freeCell(s, random, false);
    s.bonusTime = BONUS_S;
  }
}

function eatBonus(s: SnakeState, random: () => number): void {
  const [x, y] = centre(s.bonus);
  emitSparks(s.sparks, x, y, 18, 120, "#ffd23f", random, 0.7, 2);
  // Quicker is richer: up to 60 extra points for a fresh fruit.
  s.score += (50 + Math.round(s.bonusTime * 10)) * s.level;
  s.grow += 2;
  s.bonus = -1;
  s.bonusTime = 0;
}

/** One tick: take the next queued turn, move the head one cell, eat or crash. */
function advance(s: SnakeState, random: () => number): void {
  const d = s.queue.length > 0 ? (s.queue.shift() as number) : s.dir;
  s.dir = d;
  const head = s.body[0];
  const nx = (head % COLS) + DIR_X[d], ny = Math.floor(head / COLS) + DIR_Y[d];
  if (nx < 0 || ny < 0 || nx >= COLS || ny >= ROWS) {
    crash(s, random);
    return;
  }
  const next = cellOf(nx, ny);
  const tail = s.body[s.body.length - 1];
  const tailLeaves = s.grow === 0;
  const hit = s.grid[next];
  // Moving into the cell the tail is leaving this very tick is fine.
  if (hit === WALL || (hit === SNAKE && !(tailLeaves && next === tail))) {
    crash(s, random);
    return;
  }
  if (tailLeaves) {
    s.body.pop();
    s.grid[tail] = EMPTY;
  } else {
    s.grow -= 1;
  }
  s.body.unshift(next);
  s.grid[next] = SNAKE;
  if (next === s.food) eatFood(s, random);
  else if (next === s.bonus) eatBonus(s, random);
}

function stepSnake(s: SnakeState, input: RetroInput, dt: number, random: () => number): void {
  s.time += dt;
  s.flash = Math.max(0, s.flash - dt * 2.5);
  s.shake = Math.max(0, s.shake - dt);
  stepSparks(s.sparks, dt);
  if (s.over) return;
  const p = input.pressed;
  if (p.up) queueTurn(s, UP);
  if (p.down) queueTurn(s, DOWN);
  if (p.left) queueTurn(s, LEFT);
  if (p.right) queueTurn(s, RIGHT);
  if (s.bonus >= 0) {
    s.bonusTime -= dt;
    if (s.bonusTime <= 0) {
      const [x, y] = centre(s.bonus);
      emitSparks(s.sparks, x, y, 6, 30, "#8a6a10", random, 0.4, 2);
      s.bonus = -1;
      s.bonusTime = 0;
    }
  }
  s.clock += dt;
  const every = moveInterval(s);
  if (s.clock >= every) {
    s.clock -= every;
    advance(s, random);
  }
}

// ---- drawing -------------------------------------------------------------

const SHADES = 16;
/** Body colours from head to tail, built once so a frame allocates no strings. */
const LIVE_SHADES: string[] = [];
const DEAD_SHADES: string[] = [];
for (let i = 0; i < SHADES; i += 1) {
  const t = i / (SHADES - 1);
  const mix = (a: number, b: number) => Math.round(a + (b - a) * t);
  LIVE_SHADES.push(`rgb(${mix(93, 16)},${mix(255, 150)},${mix(156, 140)})`);
  DEAD_SHADES.push(`rgb(${mix(255, 90)},${mix(93, 30)},${mix(122, 50)})`);
}

function drawGrid(ctx: CanvasRenderingContext2D): void {
  ctx.fillStyle = "#04070c";
  ctx.fillRect(0, 0, WIDTH, HEIGHT);
  ctx.strokeStyle = "rgba(34,224,122,0.08)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = CELL; x < WIDTH; x += CELL) { ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, HEIGHT); }
  for (let y = CELL; y < HEIGHT; y += CELL) { ctx.moveTo(0, y + 0.5); ctx.lineTo(WIDTH, y + 0.5); }
  ctx.stroke();
  ctx.strokeStyle = "rgba(93,255,156,0.45)";
  ctx.strokeRect(0.5, 0.5, WIDTH - 1, HEIGHT - 1);
}

function drawObstacles(ctx: CanvasRenderingContext2D, s: SnakeState): void {
  for (const c of s.obstacles) {
    const x = (c % COLS) * CELL, y = Math.floor(c / COLS) * CELL;
    ctx.fillStyle = "#7a1060";
    ctx.fillRect(x, y, CELL, CELL);
    ctx.fillStyle = "#ff4fd8";
    ctx.fillRect(x + 1, y + 1, CELL - 2, CELL - 2);
    ctx.fillStyle = "#3a0630";
    ctx.fillRect(x + 3, y + 3, CELL - 6, CELL - 6);
    ctx.fillStyle = "#ffb3f0";
    ctx.fillRect(x + 1, y + 1, CELL - 2, 1);
  }
}

function drawFruit(ctx: CanvasRenderingContext2D, s: SnakeState, info: { time: number; reduced: boolean }): void {
  if (s.food >= 0) {
    const x = (s.food % COLS) * CELL, y = Math.floor(s.food / COLS) * CELL;
    const pulse = info.reduced ? 0 : Math.round(Math.sin(info.time * 6) + 1);
    ctx.globalAlpha = 0.25;
    ctx.fillStyle = "#ff4d6d";
    ctx.fillRect(x - 1 - pulse, y - 1 - pulse, CELL + 2 + pulse * 2, CELL + 2 + pulse * 2);
    ctx.globalAlpha = 1;
    ctx.fillRect(x + 2, y + 3, CELL - 4, CELL - 4);
    ctx.fillRect(x + 3, y + 2, CELL - 6, CELL - 2);
    ctx.fillStyle = "#ffc2cc";
    ctx.fillRect(x + 3, y + 4, 2, 2);
    ctx.fillStyle = "#5dff9c";
    ctx.fillRect(x + 5, y, 2, 2);
  }
  if (s.bonus >= 0) {
    const [cx, cy] = centre(s.bonus);
    // Blinks for its last second and a half; fades instead when motion is reduced.
    const ending = s.bonusTime < 1.5;
    if (ending && !info.reduced && Math.floor(s.time * 8) % 2 === 1) return;
    ctx.globalAlpha = ending && info.reduced ? 0.5 : 1;
    ctx.fillStyle = "#ffd23f";
    ctx.beginPath();
    ctx.moveTo(cx, cy - 5); ctx.lineTo(cx + 5, cy); ctx.lineTo(cx, cy + 5); ctx.lineTo(cx - 5, cy);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "#fff6c8";
    ctx.fillRect(cx - 1, cy - 3, 2, 2);
    ctx.strokeStyle = "#ffd23f";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, 8, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * clamp(s.bonusTime / BONUS_S, 0, 1));
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
}

function drawSnake(ctx: CanvasRenderingContext2D, s: SnakeState): void {
  const shades = s.over ? DEAD_SHADES : LIVE_SHADES;
  const n = s.body.length;
  for (let i = n - 1; i >= 0; i -= 1) {
    const c = s.body[i];
    const x = (c % COLS) * CELL, y = Math.floor(c / COLS) * CELL;
    const shade = shades[Math.min(SHADES - 1, Math.floor((i / Math.max(1, n - 1)) * (SHADES - 1)))];
    ctx.fillStyle = shade;
    ctx.fillRect(x + 1, y + 1, CELL - 2, CELL - 2);
    // Bridge to the next segment, so the body reads as one tube.
    if (i + 1 < n) {
      const nc = s.body[i + 1];
      const dx = (nc % COLS) - (c % COLS), dy = Math.floor(nc / COLS) - Math.floor(c / COLS);
      if (Math.abs(dx) + Math.abs(dy) === 1) ctx.fillRect(x + 1 + dx * 2, y + 1 + dy * 2, CELL - 2, CELL - 2);
    }
  }
  // The head: a soft halo and two eyes looking where it goes.
  const head = s.body[0];
  const hx = (head % COLS) * CELL, hy = Math.floor(head / COLS) * CELL;
  ctx.globalAlpha = 0.3;
  ctx.fillStyle = shades[0];
  ctx.fillRect(hx - 2, hy - 2, CELL + 4, CELL + 4);
  ctx.globalAlpha = 1;
  ctx.fillStyle = s.over ? "#ffffff" : "#d9ffe9";
  ctx.fillRect(hx + 1, hy + 1, CELL - 2, CELL - 2);
  ctx.fillStyle = shades[0];
  ctx.fillRect(hx + 2, hy + 2, CELL - 4, CELL - 4);
  ctx.fillStyle = "#04070c";
  const d = s.dir;
  const fx = DIR_X[d], fy = DIR_Y[d];
  const ex = hx + CELL / 2 + fx * 2 - 1, ey = hy + CELL / 2 + fy * 2 - 1;
  ctx.fillRect(ex + fy * 2, ey + fx * 2, 2, 2);
  ctx.fillRect(ex - fy * 2, ey - fx * 2, 2, 2);
}

function drawSnakeGame(ctx: CanvasRenderingContext2D, s: SnakeState, info: { time: number; reduced: boolean; idle: boolean }): void {
  ctx.save();
  if (!info.reduced && s.shake > 0) {
    const m = s.shake * 8;
    ctx.translate(Math.round(Math.sin(s.time * 90) * m), Math.round(Math.cos(s.time * 70) * m));
  }
  drawGrid(ctx);
  drawObstacles(ctx, s);
  drawFruit(ctx, s, info);
  drawSnake(ctx, s);
  drawSparks(ctx, s.sparks);
  ctx.restore();
  if (!info.reduced && s.flash > 0) {
    ctx.globalAlpha = s.flash * 0.22;
    ctx.fillStyle = s.flashColor;
    ctx.fillRect(0, 0, WIDTH, HEIGHT);
    ctx.globalAlpha = 1;
  }
  drawScanlines(ctx, WIDTH, HEIGHT, 0.1);
}

const neonSnake: RetroGame<SnakeState> = {
  id: "neon-snake",
  width: WIDTH,
  height: HEIGHT,
  create: createSnake,
  step: stepSnake,
  draw: drawSnakeGame,
  status: (s) => ({ score: s.score, level: s.level, over: s.over, won: s.won }),
};

export default neonSnake;
