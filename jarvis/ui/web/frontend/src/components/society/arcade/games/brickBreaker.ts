/**
 * "Brick Breaker": keep the ball in play with a paddle and knock out every
 * brick on the board.
 *
 * Where the ball meets the paddle sets its bounce angle (centre = straight
 * up, the ends = steep), so aiming is a skill. Bricks take one to three hits
 * and show their damage. The ball moves in substeps no longer than half its
 * radius, so even a very fast ball cannot pass through a brick between two
 * frames. Broken bricks sometimes drop a capsule: wide paddle, multi-ball,
 * slow ball or an extra life — catch it with the paddle. Six board layouts,
 * then they repeat with a faster ball.
 */
import type { RetroGame, RetroInput } from "../retroGame";
import { clamp, drawScanlines, drawSparks, emitSparks, sparkPool, stepSparks, type SparkPool } from "./games1Kit";

export const WIDTH = 320;
export const HEIGHT = 240;
export const COLS = 12;
export const ROWS = 8;
export const BRICK_W = 24;
export const BRICK_H = 10;
const GAP = 2;
export const FIELD_X = (WIDTH - (COLS * (BRICK_W + GAP) - GAP)) / 2;
export const FIELD_Y = 26;
export const PADDLE_Y = 222;
export const PADDLE_H = 6;
export const PADDLE_W = 44;
export const WIDE_W = 68;
const PADDLE_SPEED = 260;
export const BALL_R = 3;
/** Longest move per collision substep: half the ball's radius, well under a brick's height. */
const SUBSTEP = BALL_R / 2;
export const MAX_BALLS = 6;
const MAX_DROPS = 6;
/** The ball never goes faster than this (px/s), however long the rally. */
export const MAX_SPEED = 330;
/** Steepest bounce off the paddle's ends, from straight up. */
const MAX_BOUNCE = 1.05;
/** The ball always keeps at least this much of its speed vertical, so it cannot creep sideways forever. */
const MIN_VERTICAL = 0.3;
export const START_LIVES = 3;
export const MAX_LIVES = 5;
export const DROP_CHANCE = 0.16;
export const DROP_SPEED = 55;
export const WIDE_S = 14;
export const SLOW_S = 10;
export const SLOW_FACTOR = 0.65;

export type PowerKind = "wide" | "multi" | "slow" | "life";

/** The boards, top row first; digits are hit points, dots are gaps. */
export const LAYOUTS: readonly (readonly string[])[] = [
  [
    "............",
    "333333333333",
    "222222222222",
    "222222222222",
    "111111111111",
    "111111111111",
    "111111111111",
    "............",
  ],
  [
    ".....33.....",
    "....2222....",
    "...222222...",
    "..11111111..",
    ".1111111111.",
    "111111111111",
    "............",
    "............",
  ],
  [
    "3.3.3.3.3.3.",
    ".2.2.2.2.2.2",
    "1.1.1.1.1.1.",
    ".1.1.1.1.1.1",
    "2.2.2.2.2.2.",
    ".1.1.1.1.1.1",
    "............",
    "............",
  ],
  [
    ".....22.....",
    "....1331....",
    "...123321...",
    "..12333321..",
    "...123321...",
    "....1331....",
    ".....22.....",
    "............",
  ],
  [
    "333333333333",
    "3..........3",
    "3.22222222.3",
    "3.21111112.3",
    "3.21111112.3",
    "3.22222222.3",
    "3..........3",
    "3333....3333",
  ],
  [
    "1111........",
    "..2222......",
    "....3333....",
    "......2222..",
    "........1111",
    "......2222..",
    "....3333....",
    "..2222......",
  ],
];

export interface Ball {
  alive: boolean;
  /** Riding on the paddle, waiting for launch. */
  stuck: boolean;
  x: number; y: number; vx: number; vy: number;
}

export interface Drop {
  alive: boolean;
  kind: PowerKind;
  x: number; y: number;
}

export interface BrickState {
  /** Hit points per brick, row-major; 0 = gone. */
  bricks: Int8Array;
  /** Seconds of white flash per brick after a hit. */
  hitFlash: Float32Array;
  /** Bricks still standing. */
  left: number;
  /** Paddle centre and its current (easing) width. */
  paddleX: number;
  paddleW: number;
  wideTime: number;
  slowTime: number;
  /** Fixed pools; `alive` marks the ones in play. */
  balls: Ball[];
  drops: Drop[];
  /** The ball's speed before slow-down (px/s); grows a little with every paddle hit. */
  speed: number;
  /** Bricks broken since the ball last touched the paddle. */
  combo: number;
  lives: number;
  level: number;
  score: number;
  over: boolean;
  /** Last pointer x seen; the paddle follows the pointer only when it moves. */
  pointerX: number | null;
  time: number;
  shake: number;
  flash: number;
  flashColor: string;
  sparks: SparkPool;
}

/** The base ball speed of a level: a little faster per board, much faster each time the boards repeat. */
export function levelSpeed(level: number): number {
  const board = (level - 1) % LAYOUTS.length;
  const loop = Math.floor((level - 1) / LAYOUTS.length);
  return Math.min(MAX_SPEED, 140 + board * 8 + loop * 45);
}

export function brickIndex(col: number, row: number): number {
  return row * COLS + col;
}

export function brickX(col: number): number {
  return FIELD_X + col * (BRICK_W + GAP);
}

export function brickY(row: number): number {
  return FIELD_Y + row * (BRICK_H + GAP);
}

export function liveBalls(s: BrickState): number {
  let n = 0;
  for (const b of s.balls) if (b.alive) n += 1;
  return n;
}

function resetBall(s: BrickState): void {
  for (const b of s.balls) b.alive = false;
  const b = s.balls[0];
  b.alive = true;
  b.stuck = true;
  b.vx = 0;
  b.vy = 0;
  b.x = s.paddleX;
  b.y = PADDLE_Y - BALL_R;
  for (const d of s.drops) d.alive = false;
  s.wideTime = 0;
  s.slowTime = 0;
  s.combo = 0;
}

/** Lay out the board for `s.level` and put a fresh ball on the paddle. */
export function loadLevel(s: BrickState): void {
  const layout = LAYOUTS[(s.level - 1) % LAYOUTS.length];
  s.left = 0;
  for (let r = 0; r < ROWS; r += 1) {
    for (let c = 0; c < COLS; c += 1) {
      const ch = layout[r][c];
      const hp = ch === "." ? 0 : Number(ch);
      s.bricks[brickIndex(c, r)] = hp;
      s.hitFlash[brickIndex(c, r)] = 0;
      if (hp > 0) s.left += 1;
    }
  }
  s.speed = levelSpeed(s.level);
  resetBall(s);
}

function createBricks(): BrickState {
  const s: BrickState = {
    bricks: new Int8Array(COLS * ROWS), hitFlash: new Float32Array(COLS * ROWS), left: 0,
    paddleX: WIDTH / 2, paddleW: PADDLE_W, wideTime: 0, slowTime: 0,
    balls: [], drops: [], speed: levelSpeed(1), combo: 0,
    lives: START_LIVES, level: 1, score: 0, over: false, pointerX: null,
    time: 0, shake: 0, flash: 0, flashColor: "#ffffff", sparks: sparkPool(160),
  };
  for (let i = 0; i < MAX_BALLS; i += 1) s.balls.push({ alive: false, stuck: false, x: 0, y: 0, vx: 0, vy: 0 });
  for (let i = 0; i < MAX_DROPS; i += 1) s.drops.push({ alive: false, kind: "wide", x: 0, y: 0 });
  loadLevel(s);
  return s;
}

/** The speed every moving ball has right now. */
export function currentSpeed(s: BrickState): number {
  return Math.min(MAX_SPEED, s.speed) * (s.slowTime > 0 ? SLOW_FACTOR : 1);
}

/** Point a ball at `angle` from straight up (negative = left) at `speed`. */
function aim(b: Ball, angle: number, speed: number): void {
  b.vx = Math.sin(angle) * speed;
  b.vy = -Math.cos(angle) * speed;
}

/** Rescale a ball to `speed`, keeping at least MIN_VERTICAL of it vertical. */
function normalise(b: Ball, speed: number): void {
  let len = Math.hypot(b.vx, b.vy);
  if (len < 1e-6) { b.vx = 0; b.vy = -1; len = 1; }
  let vx = b.vx / len, vy = b.vy / len;
  if (Math.abs(vy) < MIN_VERTICAL) {
    vy = vy < 0 ? -MIN_VERTICAL : MIN_VERTICAL;
    vx = Math.sign(vx || 1) * Math.sqrt(1 - MIN_VERTICAL * MIN_VERTICAL);
  }
  b.vx = vx * speed;
  b.vy = vy * speed;
}

const ROW_COLOURS = ["#ff3d7f", "#ff7a3d", "#ffc43d", "#9be15d", "#3de0c4", "#3da2ff", "#8a6bff", "#e05dff"];
const ROW_LIGHT = ["#ff9cc0", "#ffb48c", "#ffe38c", "#cdf5a8", "#9cf2e3", "#9ccfff", "#c2b3ff", "#f2acff"];
const ROW_DARK = ["#8a1640", "#8a3a14", "#8a6614", "#4a7a24", "#14806c", "#14548a", "#3e2a9a", "#7a1f8a"];
const DROP_COLOURS: Record<PowerKind, string> = { wide: "#3da2ff", multi: "#ffc43d", slow: "#3de0c4", life: "#ff3d7f" };

function spawnDrop(s: BrickState, x: number, y: number, random: () => number): void {
  const slot = s.drops.find((d) => !d.alive);
  if (!slot) return;
  const r = random();
  slot.kind = r < 0.32 ? "wide" : r < 0.62 ? "multi" : r < 0.9 ? "slow" : "life";
  slot.alive = true;
  slot.x = x;
  slot.y = y;
}

/** Damage the brick at `i`; scores, breaks, maybe drops a capsule, may clear the board. */
function hitBrick(s: BrickState, i: number, random: () => number): void {
  const hpBefore = s.bricks[i];
  s.bricks[i] = hpBefore - 1;
  s.hitFlash[i] = 0.12;
  const col = i % COLS, row = Math.floor(i / COLS);
  const cx = brickX(col) + BRICK_W / 2, cy = brickY(row) + BRICK_H / 2;
  if (s.bricks[i] > 0) {
    s.score += 10;
    emitSparks(s.sparks, cx, cy, 3, 50, "#ffffff", random, 0.25, 1);
    return;
  }
  s.score += 30 + 20 * hpBefore + 10 * Math.min(s.combo, 10);
  s.combo += 1;
  s.left -= 1;
  emitSparks(s.sparks, cx, cy, 12, 110, ROW_COLOURS[row], random, 0.55, 2);
  if (random() < DROP_CHANCE) spawnDrop(s, cx, cy, random);
}

/**
 * Circle against the bricks around the ball. On a hit the ball is pushed out
 * along the shallower overlap and reflected on that axis; one brick per substep.
 */
function collideBricks(s: BrickState, b: Ball, random: () => number): boolean {
  const pitchX = BRICK_W + GAP, pitchY = BRICK_H + GAP;
  const c0 = Math.max(0, Math.floor((b.x - BALL_R - FIELD_X) / pitchX));
  const c1 = Math.min(COLS - 1, Math.floor((b.x + BALL_R - FIELD_X) / pitchX));
  const r0 = Math.max(0, Math.floor((b.y - BALL_R - FIELD_Y) / pitchY));
  const r1 = Math.min(ROWS - 1, Math.floor((b.y + BALL_R - FIELD_Y) / pitchY));
  for (let r = r0; r <= r1; r += 1) {
    for (let c = c0; c <= c1; c += 1) {
      const i = brickIndex(c, r);
      if (s.bricks[i] <= 0) continue;
      const bx = brickX(c), by = brickY(r);
      const px = clamp(b.x, bx, bx + BRICK_W), py = clamp(b.y, by, by + BRICK_H);
      const dx = b.x - px, dy = b.y - py;
      if (dx * dx + dy * dy > BALL_R * BALL_R) continue;
      const overlapX = Math.min(b.x + BALL_R - bx, bx + BRICK_W - (b.x - BALL_R));
      const overlapY = Math.min(b.y + BALL_R - by, by + BRICK_H - (b.y - BALL_R));
      if (overlapX < overlapY) {
        const fromLeft = b.x < bx + BRICK_W / 2;
        b.vx = fromLeft ? -Math.abs(b.vx) : Math.abs(b.vx);
        b.x = fromLeft ? bx - BALL_R : bx + BRICK_W + BALL_R;
      } else {
        const fromAbove = b.y < by + BRICK_H / 2;
        b.vy = fromAbove ? -Math.abs(b.vy) : Math.abs(b.vy);
        b.y = fromAbove ? by - BALL_R : by + BRICK_H + BALL_R;
      }
      hitBrick(s, i, random);
      return true;
    }
  }
  return false;
}

/** Move one ball through walls, paddle and bricks in short substeps. */
export function moveBall(s: BrickState, b: Ball, dt: number, random: () => number): void {
  const n = Math.max(1, Math.ceil((Math.hypot(b.vx, b.vy) * dt) / SUBSTEP));
  const h = dt / n;
  for (let k = 0; k < n; k += 1) {
    b.x += b.vx * h;
    b.y += b.vy * h;
    if (b.x < BALL_R) { b.x = BALL_R; b.vx = Math.abs(b.vx); }
    else if (b.x > WIDTH - BALL_R) { b.x = WIDTH - BALL_R; b.vx = -Math.abs(b.vx); }
    if (b.y < BALL_R) { b.y = BALL_R; b.vy = Math.abs(b.vy); }
    const half = s.paddleW / 2;
    if (b.vy > 0 && b.y + BALL_R >= PADDLE_Y && b.y + BALL_R <= PADDLE_Y + PADDLE_H
        && Math.abs(b.x - s.paddleX) <= half + BALL_R) {
      const offset = clamp((b.x - s.paddleX) / half, -1, 1);
      aim(b, offset * MAX_BOUNCE, Math.hypot(b.vx, b.vy));
      b.y = PADDLE_Y - BALL_R;
      s.combo = 0;
      s.speed = Math.min(MAX_SPEED, s.speed + 2);
      emitSparks(s.sparks, b.x, PADDLE_Y, 4, 60, "#ffd1e0", random, 0.25, 1);
    }
    if (collideBricks(s, b, random) && s.left === 0) return;
    if (b.y - BALL_R > HEIGHT) { b.alive = false; return; }
  }
}

function launch(b: Ball, s: BrickState, random: () => number): void {
  b.stuck = false;
  let angle = (random() - 0.5) * 0.8;
  if (Math.abs(angle) < 0.12) angle = angle < 0 ? -0.12 : 0.12;
  aim(b, angle, currentSpeed(s));
}

function applyPower(s: BrickState, kind: PowerKind, random: () => number): void {
  s.score += 50;
  if (kind === "wide") s.wideTime = WIDE_S;
  else if (kind === "slow") s.slowTime = SLOW_S;
  else if (kind === "life") s.lives = Math.min(MAX_LIVES, s.lives + 1);
  else {
    const source = s.balls.find((b) => b.alive);
    if (!source) return;
    if (source.stuck) launch(source, s, random);
    const speed = Math.hypot(source.vx, source.vy);
    const base = Math.atan2(source.vx, -source.vy);
    for (const turn of [-0.4, 0.4]) {
      const slot = s.balls.find((b) => !b.alive);
      if (!slot) break;
      slot.alive = true;
      slot.stuck = false;
      slot.x = source.x;
      slot.y = source.y;
      aim(slot, base + turn, speed);
      normalise(slot, speed);
    }
  }
}

function loseLife(s: BrickState): void {
  s.lives -= 1;
  s.shake = 0.4;
  s.flash = 0.8;
  s.flashColor = "#ff3d5a";
  if (s.lives <= 0) {
    s.lives = 0;
    s.over = true;
    return;
  }
  s.speed = levelSpeed(s.level);
  resetBall(s);
}

function stepBricks(s: BrickState, input: RetroInput, dt: number, random: () => number): void {
  s.time += dt;
  s.shake = Math.max(0, s.shake - dt);
  s.flash = Math.max(0, s.flash - dt * 2);
  for (let i = 0; i < s.hitFlash.length; i += 1) if (s.hitFlash[i] > 0) s.hitFlash[i] -= dt;
  stepSparks(s.sparks, dt, 160);
  if (s.over) return;
  s.wideTime = Math.max(0, s.wideTime - dt);
  s.slowTime = Math.max(0, s.slowTime - dt);

  // Paddle: keys, or the pointer whenever it moves.
  const dir = (input.right ? 1 : 0) - (input.left ? 1 : 0);
  s.paddleX += dir * PADDLE_SPEED * dt;
  const pointer = input.pointer;
  if (pointer && pointer.x !== s.pointerX) s.paddleX = pointer.x;
  s.pointerX = pointer ? pointer.x : null;
  const targetW = s.wideTime > 0 ? WIDE_W : PADDLE_W;
  s.paddleW += clamp(targetW - s.paddleW, -120 * dt, 120 * dt);
  s.paddleX = clamp(s.paddleX, s.paddleW / 2, WIDTH - s.paddleW / 2);

  const fire = input.pressed.a || (pointer?.clicked ?? false);
  const speed = currentSpeed(s);
  for (const b of s.balls) {
    if (!b.alive) continue;
    if (b.stuck) {
      b.x = s.paddleX;
      b.y = PADDLE_Y - BALL_R;
      if (fire) launch(b, s, random);
      continue;
    }
    normalise(b, speed);
    moveBall(s, b, dt, random);
    if (s.left === 0) break;
  }

  if (s.left === 0) {
    s.score += 500 * s.level;
    s.level += 1;
    s.flash = 1;
    s.flashColor = "#ffffff";
    emitSparks(s.sparks, WIDTH / 2, HEIGHT / 2, 40, 160, "#ffc43d", random, 0.9, 2);
    loadLevel(s);
    return;
  }

  for (const d of s.drops) {
    if (!d.alive) continue;
    d.y += DROP_SPEED * dt;
    if (d.y + 4 >= PADDLE_Y && d.y - 4 <= PADDLE_Y + PADDLE_H && Math.abs(d.x - s.paddleX) <= s.paddleW / 2 + 8) {
      d.alive = false;
      emitSparks(s.sparks, d.x, PADDLE_Y, 10, 90, DROP_COLOURS[d.kind], random, 0.5, 2);
      applyPower(s, d.kind, random);
    } else if (d.y - 4 > HEIGHT) {
      d.alive = false;
    }
  }

  if (liveBalls(s) === 0) loseLife(s);
}

// ---- drawing -------------------------------------------------------------

function drawBricks(ctx: CanvasRenderingContext2D, s: BrickState): void {
  for (let r = 0; r < ROWS; r += 1) {
    for (let c = 0; c < COLS; c += 1) {
      const i = brickIndex(c, r);
      const hp = s.bricks[i];
      if (hp <= 0) continue;
      const x = brickX(c), y = brickY(r);
      if (hp >= 3) {
        // Steel: grey with rivets.
        ctx.fillStyle = "#5c6378";
        ctx.fillRect(x, y, BRICK_W, BRICK_H);
        ctx.fillStyle = "#c9cfdd";
        ctx.fillRect(x, y, BRICK_W, 2);
        ctx.fillStyle = "#2c3040";
        ctx.fillRect(x, y + BRICK_H - 1, BRICK_W, 1);
        ctx.fillStyle = "#9aa1b5";
        ctx.fillRect(x + 2, y + 4, 2, 2);
        ctx.fillRect(x + BRICK_W - 4, y + 4, 2, 2);
      } else {
        ctx.fillStyle = ROW_COLOURS[r];
        ctx.fillRect(x, y, BRICK_W, BRICK_H);
        ctx.fillStyle = ROW_LIGHT[r];
        ctx.fillRect(x, y, BRICK_W, 2);
        ctx.fillStyle = ROW_DARK[r];
        ctx.fillRect(x, y + BRICK_H - 2, BRICK_W, 2);
        if (hp === 2) {
          ctx.fillStyle = ROW_LIGHT[r];
          ctx.fillRect(x + 4, y + 4, BRICK_W - 8, 2);
        }
      }
      if (s.hitFlash[i] > 0) {
        ctx.globalAlpha = clamp(s.hitFlash[i] / 0.12, 0, 1);
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(x, y, BRICK_W, BRICK_H);
        ctx.globalAlpha = 1;
      }
    }
  }
}

function drawPaddle(ctx: CanvasRenderingContext2D, s: BrickState): void {
  const w = Math.round(s.paddleW), x = Math.round(s.paddleX - w / 2);
  const ends = s.wideTime > 0 ? "#3da2ff" : "#ff3d7f";
  ctx.globalAlpha = 0.25;
  ctx.fillStyle = ends;
  ctx.fillRect(x - 2, PADDLE_Y - 2, w + 4, PADDLE_H + 4);
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#ffd1e0";
  ctx.fillRect(x, PADDLE_Y, w, PADDLE_H);
  ctx.fillStyle = ends;
  ctx.fillRect(x, PADDLE_Y, 6, PADDLE_H);
  ctx.fillRect(x + w - 6, PADDLE_Y, 6, PADDLE_H);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(x + 6, PADDLE_Y, w - 12, 1);
  ctx.fillStyle = "#8a5566";
  ctx.fillRect(x, PADDLE_Y + PADDLE_H - 1, w, 1);
}

function drawBalls(ctx: CanvasRenderingContext2D, s: BrickState): void {
  const colour = s.slowTime > 0 ? "#9cf2e3" : "#ffffff";
  for (const b of s.balls) {
    if (!b.alive) continue;
    if (!b.stuck) {
      ctx.globalAlpha = 0.3;
      ctx.fillStyle = colour;
      ctx.fillRect(Math.round(b.x - b.vx * 0.03) - 2, Math.round(b.y - b.vy * 0.03) - 2, 4, 4);
      ctx.globalAlpha = 1;
    }
    ctx.fillStyle = colour;
    ctx.beginPath();
    ctx.arc(b.x, b.y, BALL_R, 0, Math.PI * 2);
    ctx.fill();
  }
}

/** A capsule with a little icon: arrows (wide), three dots (multi), two bars (slow), a heart (life). */
function drawDrop(ctx: CanvasRenderingContext2D, d: Drop): void {
  const x = Math.round(d.x), y = Math.round(d.y);
  ctx.fillStyle = DROP_COLOURS[d.kind];
  ctx.fillRect(x - 8, y - 4, 16, 8);
  ctx.fillRect(x - 9, y - 3, 18, 6);
  ctx.fillStyle = "#0b0611";
  if (d.kind === "wide") {
    ctx.fillRect(x - 5, y - 1, 10, 2);
    ctx.fillRect(x - 6, y - 2, 1, 4);
    ctx.fillRect(x + 5, y - 2, 1, 4);
  } else if (d.kind === "multi") {
    ctx.fillRect(x - 5, y - 1, 2, 2);
    ctx.fillRect(x - 1, y - 1, 2, 2);
    ctx.fillRect(x + 3, y - 1, 2, 2);
  } else if (d.kind === "slow") {
    ctx.fillRect(x - 3, y - 2, 2, 4);
    ctx.fillRect(x + 1, y - 2, 2, 4);
  } else {
    ctx.fillRect(x - 3, y - 2, 2, 2);
    ctx.fillRect(x + 1, y - 2, 2, 2);
    ctx.fillRect(x - 3, y - 1, 6, 2);
    ctx.fillRect(x - 1, y + 1, 2, 1);
  }
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(x - 7, y - 4, 14, 1);
}

function drawBrickGame(ctx: CanvasRenderingContext2D, s: BrickState, info: { time: number; reduced: boolean; idle: boolean }): void {
  ctx.save();
  if (!info.reduced && s.shake > 0) {
    const m = s.shake * 7;
    ctx.translate(Math.round(Math.sin(s.time * 83) * m), Math.round(Math.cos(s.time * 67) * m));
  }
  ctx.fillStyle = "#0b0611";
  ctx.fillRect(-8, -8, WIDTH + 16, HEIGHT + 16);
  // Soft vertical bands and neon side rails.
  ctx.fillStyle = "#120a1c";
  for (let x = 0; x < WIDTH; x += 40) ctx.fillRect(x, 0, 20, HEIGHT);
  ctx.globalAlpha = 0.6;
  ctx.fillStyle = "#ff3d7f";
  ctx.fillRect(0, 0, 1, HEIGHT);
  ctx.fillRect(WIDTH - 1, 0, 1, HEIGHT);
  ctx.fillRect(0, 0, WIDTH, 1);
  ctx.globalAlpha = 1;
  drawBricks(ctx, s);
  for (const d of s.drops) if (d.alive) drawDrop(ctx, d);
  drawPaddle(ctx, s);
  drawBalls(ctx, s);
  drawSparks(ctx, s.sparks);
  ctx.restore();
  if (!info.reduced && s.flash > 0) {
    ctx.globalAlpha = s.flash * 0.2;
    ctx.fillStyle = s.flashColor;
    ctx.fillRect(0, 0, WIDTH, HEIGHT);
    ctx.globalAlpha = 1;
  }
  drawScanlines(ctx, WIDTH, HEIGHT, 0.1);
}

const brickBreaker: RetroGame<BrickState> = {
  id: "brick-breaker",
  width: WIDTH,
  height: HEIGHT,
  pointer: true,
  create: () => createBricks(),
  step: stepBricks,
  draw: drawBrickGame,
  status: (s) => ({ score: s.score, lives: s.lives, level: s.level, over: s.over }),
};

export default brickBreaker;
