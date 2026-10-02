/**
 * Small helpers shared by Block Drop, Maze Muncher and Desert Dash.
 *
 * A fixed-size spark pool gives the games their juice (line-clear sparks,
 * dust, bursts) without allocating per frame: the pool is created once with
 * the run and dead slots are reused. Colour mixing turns a palette shift into
 * a smooth blend instead of a hard cut, and `drawDigits` writes numbers (never
 * words) in the arcade face.
 */
import { retroFont } from "../retroGame";

export interface Spark {
  x: number; y: number; vx: number; vy: number;
  /** Seconds left; a spark with `life <= 0` is a free slot. */
  life: number;
  max: number;
  size: number;
  color: string;
}

export function sparkPool(capacity: number): Spark[] {
  const pool: Spark[] = [];
  for (let i = 0; i < capacity; i += 1) pool.push({ x: 0, y: 0, vx: 0, vy: 0, life: 0, max: 1, size: 1, color: "#ffffff" });
  return pool;
}

/** Light a spark in a free slot, or in the one closest to dying when all are lit (the pool never grows). */
export function emitSpark(pool: Spark[], x: number, y: number, vx: number, vy: number, life: number, size: number, color: string): void {
  if (pool.length === 0) return;
  let slot = pool[0];
  for (let i = 0; i < pool.length; i += 1) {
    const s = pool[i];
    if (s.life <= 0) { slot = s; break; }
    if (s.life < slot.life) slot = s;
  }
  slot.x = x; slot.y = y; slot.vx = vx; slot.vy = vy;
  slot.life = life; slot.max = life; slot.size = size; slot.color = color;
}

/** Move every lit spark; `gravity` pulls down in pixels per second squared. */
export function stepSparks(pool: Spark[], dt: number, gravity: number): void {
  for (let i = 0; i < pool.length; i += 1) {
    const s = pool[i];
    if (s.life <= 0) continue;
    s.life -= dt;
    s.vy += gravity * dt;
    s.x += s.vx * dt;
    s.y += s.vy * dt;
  }
}

export function clearSparks(pool: Spark[]): void {
  for (let i = 0; i < pool.length; i += 1) pool[i].life = 0;
}

export function liveSparks(pool: Spark[]): number {
  let n = 0;
  for (let i = 0; i < pool.length; i += 1) if (pool[i].life > 0) n += 1;
  return n;
}

/** Square sparks that fade out over their life. */
export function drawSparks(ctx: CanvasRenderingContext2D, pool: Spark[]): void {
  for (let i = 0; i < pool.length; i += 1) {
    const s = pool[i];
    if (s.life <= 0) continue;
    ctx.globalAlpha = Math.max(0, Math.min(1, s.life / s.max));
    ctx.fillStyle = s.color;
    const half = s.size / 2;
    ctx.fillRect(Math.round(s.x - half), Math.round(s.y - half), s.size, s.size);
  }
  ctx.globalAlpha = 1;
}

/** A number in the arcade face. Only digits and symbols ever reach the canvas. */
export function drawDigits(
  ctx: CanvasRenderingContext2D, value: number | string, x: number, y: number, px: number, color: string,
  align: CanvasTextAlign = "left", weight = 400,
): void {
  ctx.font = retroFont(px, weight);
  ctx.textAlign = align;
  ctx.textBaseline = "top";
  ctx.fillStyle = color;
  ctx.fillText(typeof value === "number" ? String(Math.floor(value)) : value, x, y);
}

export type Rgb = readonly [number, number, number];

export function hexRgb(hex: string): Rgb {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** `a` blended towards `b` by `t` (0..1), as a CSS colour. */
export function mixRgb(a: Rgb, b: Rgb, t: number): string {
  const k = Math.max(0, Math.min(1, t));
  const r = Math.round(a[0] + (b[0] - a[0]) * k);
  const g = Math.round(a[1] + (b[1] - a[1]) * k);
  const bl = Math.round(a[2] + (b[2] - a[2]) * k);
  return `rgb(${r},${g},${bl})`;
}

export function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}
