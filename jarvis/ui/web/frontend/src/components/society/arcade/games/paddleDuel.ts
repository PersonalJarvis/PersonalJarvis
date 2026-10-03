/**
 * "Paddle Duel": table tennis on a screen against a CPU opponent.
 *
 * You play the left paddle. Where the ball meets the paddle sets its angle,
 * and a paddle moving at the moment of the hit puts spin on the ball, which
 * then curves. Every return in a rally makes the ball a little faster. The
 * CPU is a fair opponent: it reacts late, has a top speed and aims slightly
 * off, and it gets sharper with every match. First to WIN_POINTS takes the
 * match and moves on to the next, harder CPU; losing a match ends the run.
 *
 * Score: 100 per point won, 10 per return in that rally, 1000 × level per match.
 */
import type { RetroGame, RetroInput } from "../retroGame";
import { retroFont } from "../retroGame";
import { clamp, drawScanlines, drawSparks, emitSparks, sparkPool, stepSparks, type SparkPool } from "./games1Kit";

export const WIDTH = 320;
export const HEIGHT = 240;
/** The inside edges of the top and bottom walls. */
export const TOP = 8;
export const BOTTOM = HEIGHT - 8;
export const PADDLE_W = 6;
export const PADDLE_H = 34;
/** Left edge of each paddle. */
export const PLAYER_X = 14;
export const CPU_X = WIDTH - 14 - PADDLE_W;
export const BALL = 5;
const HALF = BALL / 2;
export const PLAYER_SPEED = 230;
/** How fast the paddle chases a pointer (px/s); quicker than the keys, but not a teleport. */
const POINTER_SPEED = 420;
export const SERVE_SPEED = 150;
export const HIT_SPEEDUP = 10;
export const MAX_BALL_SPEED = 380;
/** Steepest return, from the horizontal, off the very end of a paddle. */
const MAX_BOUNCE = 1.0;
/** Spin curves the ball by this much px/s² at full spin, and fades by half each second. */
const SPIN_ACCEL = 140;
/** The ball always keeps this share of its speed horizontal, so a rally never stalls vertically. */
const MIN_HORIZONTAL = 0.55;
export const WIN_POINTS = 7;
export const SERVE_DELAY = 1.1;
export const MATCH_PAUSE = 2.2;
/** Positions remembered for the ball's trail. */
const TRAIL = 6;

export interface CpuSkill {
  /** Seconds between two looks at the ball; it steers toward the last look in between. */
  reaction: number;
  /** Top paddle speed in px/s. */
  speed: number;
  /** Largest aiming error in px, re-rolled on every return. */
  error: number;
  /** Reads bounces off the walls instead of just following the ball. */
  predicts: boolean;
}

/** How good the CPU is in match `level`: slow and sloppy at first, sharp later, never perfect. */
export function cpuSkill(level: number): CpuSkill {
  const k = level - 1;
  return {
    reaction: Math.max(0.07, 0.22 - 0.025 * k),
    speed: Math.min(285, 118 + 22 * k),
    error: Math.max(4, 16 - 2.5 * k),
    predicts: level >= 3,
  };
}

export interface DuelState {
  /** Paddle centres and their velocity in the last step (for spin). */
  playerY: number;
  playerVy: number;
  cpuY: number;
  cpuVy: number;
  cpuTarget: number;
  cpuThink: number;
  cpuError: number;
  ballX: number;
  ballY: number;
  vx: number;
  vy: number;
  /** -1..1; bends the ball's path up or down. */
  spin: number;
  speed: number;
  serving: boolean;
  serveTimer: number;
  /** +1 serves toward the CPU, -1 toward the player. */
  serveDir: number;
  /** Returns in the current rally. */
  rally: number;
  playerPoints: number;
  cpuPoints: number;
  level: number;
  score: number;
  over: boolean;
  /** Seconds left of the break after a won match. */
  matchPause: number;
  /** Where the pointer last asked the paddle to go; null when keys are in charge. */
  pointerTarget: number | null;
  lastPointerY: number | null;
  /** Which side just conceded (-1 player, 1 CPU) and how strongly it still flashes. */
  flashSide: number;
  flash: number;
  shake: number;
  time: number;
  /** Recent ball positions, x/y pairs in a ring. */
  trail: Float32Array;
  trailHead: number;
  sparks: SparkPool;
}

function centreBall(s: DuelState): void {
  s.ballX = WIDTH / 2;
  s.ballY = HEIGHT / 2;
  s.vx = 0;
  s.vy = 0;
  s.spin = 0;
  for (let i = 0; i < TRAIL; i += 1) { s.trail[i * 2] = s.ballX; s.trail[i * 2 + 1] = s.ballY; }
}

function createDuel(): DuelState {
  const s: DuelState = {
    playerY: HEIGHT / 2, playerVy: 0, cpuY: HEIGHT / 2, cpuVy: 0, cpuTarget: HEIGHT / 2, cpuThink: 0, cpuError: 0,
    ballX: WIDTH / 2, ballY: HEIGHT / 2, vx: 0, vy: 0, spin: 0, speed: SERVE_SPEED,
    serving: true, serveTimer: SERVE_DELAY, serveDir: 1, rally: 0,
    playerPoints: 0, cpuPoints: 0, level: 1, score: 0, over: false, matchPause: 0,
    pointerTarget: null, lastPointerY: null, flashSide: 0, flash: 0, shake: 0, time: 0,
    trail: new Float32Array(TRAIL * 2), trailHead: 0, sparks: sparkPool(120),
  };
  centreBall(s);
  return s;
}

/** Put the ball in play from the middle toward `serveDir`, at a random height and angle. */
export function serve(s: DuelState, random: () => number): void {
  s.serving = false;
  s.rally = 0;
  s.speed = SERVE_SPEED + (s.level - 1) * 8;
  s.ballX = WIDTH / 2;
  s.ballY = HEIGHT / 2 + (random() - 0.5) * 80;
  const angle = (random() * 2 - 1) * 0.45;
  s.vx = Math.cos(angle) * s.speed * s.serveDir;
  s.vy = Math.sin(angle) * s.speed;
  s.spin = 0;
  s.cpuError = (random() * 2 - 1) * cpuSkill(s.level).error;
}

/** Where the ball will cross the CPU's paddle face, folding wall bounces in (spin ignored). */
export function predictY(s: DuelState): number {
  if (s.vx <= 0) return s.ballY;
  const t = (CPU_X - HALF - s.ballX) / s.vx;
  const lo = TOP + HALF, span = BOTTOM - HALF - lo;
  let y = s.ballY + s.vy * t - lo;
  // Fold the straight line back into the court, like a mirror between the walls.
  const period = span * 2;
  y = ((y % period) + period) % period;
  if (y > span) y = period - y;
  return y + lo;
}

function stepCpu(s: DuelState, dt: number): void {
  const skill = cpuSkill(s.level);
  s.cpuThink -= dt;
  if (s.cpuThink <= 0) {
    s.cpuThink = skill.reaction;
    const coming = !s.serving && s.vx > 0;
    s.cpuTarget = coming ? (skill.predicts ? predictY(s) : s.ballY) + s.cpuError : HEIGHT / 2;
  }
  const away = s.serving || s.vx <= 0;
  const max = skill.speed * (away ? 0.5 : 1) * dt;
  const before = s.cpuY;
  const diff = s.cpuTarget - s.cpuY;
  if (Math.abs(diff) > 2) s.cpuY += clamp(diff, -max, max);
  s.cpuY = clamp(s.cpuY, TOP + PADDLE_H / 2, BOTTOM - PADDLE_H / 2);
  s.cpuVy = dt > 0 ? (s.cpuY - before) / dt : 0;
}

function stepPlayer(s: DuelState, input: RetroInput, dt: number): void {
  const before = s.playerY;
  const dir = (input.down ? 1 : 0) - (input.up ? 1 : 0);
  if (dir !== 0) {
    s.pointerTarget = null;
    s.playerY += dir * PLAYER_SPEED * dt;
  }
  const pointer = input.pointer;
  if (pointer && pointer.y !== s.lastPointerY) s.pointerTarget = pointer.y;
  s.lastPointerY = pointer ? pointer.y : null;
  if (dir === 0 && s.pointerTarget !== null) {
    const step = POINTER_SPEED * dt;
    s.playerY += clamp(s.pointerTarget - s.playerY, -step, step);
  }
  s.playerY = clamp(s.playerY, TOP + PADDLE_H / 2, BOTTOM - PADDLE_H / 2);
  s.playerVy = dt > 0 ? (s.playerY - before) / dt : 0;
}

/** The ball meets a paddle: angle from the contact point, spin from the paddle's motion, a little faster. */
function returnBall(s: DuelState, byPlayer: boolean, paddleY: number, paddleVy: number, random: () => number): void {
  s.rally += 1;
  s.speed = Math.min(MAX_BALL_SPEED, s.speed + HIT_SPEEDUP);
  const offset = clamp((s.ballY - paddleY) / (PADDLE_H / 2 + HALF), -1, 1);
  const angle = offset * MAX_BOUNCE;
  const dir = byPlayer ? 1 : -1;
  s.vx = Math.cos(angle) * s.speed * dir;
  s.vy = Math.sin(angle) * s.speed + paddleVy * 0.2;
  s.spin = clamp(paddleVy / 250, -1, 1);
  s.ballX = byPlayer ? PLAYER_X + PADDLE_W + HALF : CPU_X - HALF;
  if (byPlayer) s.cpuError = (random() * 2 - 1) * cpuSkill(s.level).error;
  emitSparks(s.sparks, s.ballX, s.ballY, 6 + Math.min(10, s.rally), 90, byPlayer ? "#9aa3ff" : "#ff8f6b", random, 0.35, 2);
}

/** Keep the ball at `speed` and never too steep. */
function normaliseBall(s: DuelState): void {
  const len = Math.hypot(s.vx, s.vy);
  if (len < 1e-6) return;
  let ux = s.vx / len, uy = s.vy / len;
  if (Math.abs(ux) < MIN_HORIZONTAL) {
    ux = Math.sign(ux || 1) * MIN_HORIZONTAL;
    uy = Math.sign(uy || 1) * Math.sqrt(1 - MIN_HORIZONTAL * MIN_HORIZONTAL);
  }
  s.vx = ux * s.speed;
  s.vy = uy * s.speed;
}

function pointScored(s: DuelState, byPlayer: boolean, random: () => number): void {
  emitSparks(s.sparks, clamp(s.ballX, 0, WIDTH), s.ballY, 24, 140, byPlayer ? "#9aa3ff" : "#ff8f6b", random, 0.7, 2);
  if (byPlayer) {
    s.playerPoints += 1;
    s.score += 100 + 10 * s.rally;
    s.flashSide = 1;
  } else {
    s.cpuPoints += 1;
    s.flashSide = -1;
    s.shake = 0.3;
  }
  s.flash = 1;
  s.rally = 0;
  s.serving = true;
  s.serveTimer = SERVE_DELAY;
  // The side that lost the point receives the next serve.
  s.serveDir = byPlayer ? 1 : -1;
  centreBall(s);
  if (s.playerPoints >= WIN_POINTS) {
    s.score += 1000 * s.level;
    s.level += 1;
    s.matchPause = MATCH_PAUSE;
    s.serveDir = -1;
    for (let i = 0; i < 4; i += 1) emitSparks(s.sparks, WIDTH * (0.2 + i * 0.2), HEIGHT / 2, 12, 150, i % 2 ? "#f5f5f0" : "#9aa3ff", random, 1, 2);
  } else if (s.cpuPoints >= WIN_POINTS) {
    s.over = true;
  }
}

function moveBall(s: DuelState, dt: number, random: () => number): void {
  const n = Math.max(1, Math.ceil((Math.hypot(s.vx, s.vy) * dt) / 2));
  const h = dt / n;
  for (let k = 0; k < n; k += 1) {
    s.vy += s.spin * SPIN_ACCEL * h;
    s.ballX += s.vx * h;
    s.ballY += s.vy * h;
    if (s.ballY < TOP + HALF) {
      s.ballY = TOP + HALF;
      s.vy = Math.abs(s.vy);
      s.spin *= 0.5;
      emitSparks(s.sparks, s.ballX, TOP, 3, 50, "#f5f5f0", random, 0.25, 1);
    } else if (s.ballY > BOTTOM - HALF) {
      s.ballY = BOTTOM - HALF;
      s.vy = -Math.abs(s.vy);
      s.spin *= 0.5;
      emitSparks(s.sparks, s.ballX, BOTTOM, 3, 50, "#f5f5f0", random, 0.25, 1);
    }
    const reach = PADDLE_H / 2 + HALF;
    if (s.vx < 0 && s.ballX - HALF <= PLAYER_X + PADDLE_W && s.ballX - HALF >= PLAYER_X - 2
        && Math.abs(s.ballY - s.playerY) <= reach) {
      returnBall(s, true, s.playerY, s.playerVy, random);
    } else if (s.vx > 0 && s.ballX + HALF >= CPU_X && s.ballX + HALF <= CPU_X + PADDLE_W + 2
        && Math.abs(s.ballY - s.cpuY) <= reach) {
      returnBall(s, false, s.cpuY, s.cpuVy, random);
    }
    if (s.ballX < -BALL * 2) { pointScored(s, false, random); return; }
    if (s.ballX > WIDTH + BALL * 2) { pointScored(s, true, random); return; }
  }
}

function stepDuel(s: DuelState, input: RetroInput, dt: number, random: () => number): void {
  s.time += dt;
  s.flash = Math.max(0, s.flash - dt * 2.5);
  s.shake = Math.max(0, s.shake - dt);
  stepSparks(s.sparks, dt, 40);
  if (s.over) return;
  stepPlayer(s, input, dt);
  if (s.matchPause > 0) {
    s.matchPause -= dt;
    stepCpu(s, dt);
    if (s.matchPause <= 0) {
      s.matchPause = 0;
      s.playerPoints = 0;
      s.cpuPoints = 0;
      s.serving = true;
      s.serveTimer = SERVE_DELAY;
    }
    return;
  }
  if (s.serving) {
    s.serveTimer -= dt;
    if (s.serveTimer <= 0 || input.pressed.a) serve(s, random);
  }
  stepCpu(s, dt);
  if (s.serving) return;
  s.spin *= Math.pow(0.5, dt);
  normaliseBall(s);
  moveBall(s, dt, random);
  s.trailHead = (s.trailHead + 1) % TRAIL;
  s.trail[s.trailHead * 2] = s.ballX;
  s.trail[s.trailHead * 2 + 1] = s.ballY;
}

// ---- drawing -------------------------------------------------------------

const DIGITS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"];
const PLAYER_COLOUR = "#9aa3ff";
const CPU_COLOUR = "#ff8f6b";
const LINE = "#e8e4d8";

function drawPaddle(ctx: CanvasRenderingContext2D, x: number, y: number, colour: string): void {
  const top = Math.round(y - PADDLE_H / 2);
  ctx.globalAlpha = 0.22;
  ctx.fillStyle = colour;
  ctx.fillRect(x - 2, top - 2, PADDLE_W + 4, PADDLE_H + 4);
  ctx.globalAlpha = 1;
  ctx.fillRect(x, top, PADDLE_W, PADDLE_H);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(x + 1, top + 1, 1, PADDLE_H - 2);
}

function drawDuel(ctx: CanvasRenderingContext2D, s: DuelState, info: { time: number; reduced: boolean; idle: boolean }): void {
  ctx.save();
  if (!info.reduced && s.shake > 0) {
    const m = s.shake * 6;
    ctx.translate(Math.round(Math.sin(s.time * 77) * m), Math.round(Math.cos(s.time * 61) * m));
  }
  ctx.fillStyle = "#0a0b10";
  ctx.fillRect(-8, -8, WIDTH + 16, HEIGHT + 16);
  if (!info.reduced && s.flash > 0 && s.flashSide !== 0) {
    ctx.globalAlpha = s.flash * 0.16;
    ctx.fillStyle = s.flashSide > 0 ? PLAYER_COLOUR : CPU_COLOUR;
    ctx.fillRect(s.flashSide > 0 ? WIDTH / 2 : 0, 0, WIDTH / 2, HEIGHT);
    ctx.globalAlpha = 1;
  }

  // Court: walls, centre dashes, scores and the CPU's level pips.
  ctx.fillStyle = LINE;
  ctx.fillRect(0, TOP - 2, WIDTH, 2);
  ctx.fillRect(0, BOTTOM, WIDTH, 2);
  ctx.globalAlpha = 0.35;
  for (let y = TOP + 4; y < BOTTOM - 4; y += 12) ctx.fillRect(WIDTH / 2 - 1, y, 2, 6);
  ctx.font = retroFont(28, 600);
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.fillStyle = PLAYER_COLOUR;
  ctx.fillText(DIGITS[Math.min(9, s.playerPoints)], WIDTH / 2 - 36, TOP + 8);
  ctx.fillStyle = CPU_COLOUR;
  ctx.fillText(DIGITS[Math.min(9, s.cpuPoints)], WIDTH / 2 + 36, TOP + 8);
  ctx.globalAlpha = 0.6;
  const pips = Math.min(10, s.level);
  for (let i = 0; i < pips; i += 1) ctx.fillRect(WIDTH / 2 + 36 - pips * 2 + i * 4, TOP + 42, 2, 2);
  ctx.globalAlpha = 1;

  drawPaddle(ctx, PLAYER_X, s.playerY, PLAYER_COLOUR);
  drawPaddle(ctx, CPU_X, s.cpuY, CPU_COLOUR);

  // Ball, with a trail tinted by its spin.
  const tint = s.spin > 0.15 ? PLAYER_COLOUR : s.spin < -0.15 ? CPU_COLOUR : "#f5f5f0";
  if (!s.serving && s.matchPause <= 0) {
    for (let i = 1; i < TRAIL; i += 1) {
      const j = (s.trailHead - i + TRAIL) % TRAIL;
      ctx.globalAlpha = 0.28 * (1 - i / TRAIL);
      ctx.fillStyle = tint;
      ctx.fillRect(Math.round(s.trail[j * 2] - HALF), Math.round(s.trail[j * 2 + 1] - HALF), BALL, BALL);
    }
    ctx.globalAlpha = 1;
  }
  if (s.matchPause <= 0) {
    ctx.fillStyle = "#f5f5f0";
    ctx.fillRect(Math.round(s.ballX - HALF), Math.round(s.ballY - HALF), BALL, BALL);
    if (s.serving && !info.idle) {
      // A ring that closes until the automatic serve.
      ctx.strokeStyle = s.serveDir > 0 ? PLAYER_COLOUR : CPU_COLOUR;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(s.ballX, s.ballY, 8, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * clamp(s.serveTimer / SERVE_DELAY, 0, 1));
      ctx.stroke();
    }
  }
  drawSparks(ctx, s.sparks);
  ctx.restore();
  drawScanlines(ctx, WIDTH, HEIGHT, 0.1);
}

const paddleDuel: RetroGame<DuelState> = {
  id: "paddle-duel",
  width: WIDTH,
  height: HEIGHT,
  pointer: true,
  create: () => createDuel(),
  step: stepDuel,
  draw: drawDuel,
  status: (s) => ({ score: s.score, level: s.level, over: s.over, won: false }),
};

export default paddleDuel;
