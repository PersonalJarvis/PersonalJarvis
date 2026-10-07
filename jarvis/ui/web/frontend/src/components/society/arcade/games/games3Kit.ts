/**
 * Small drawing and effect helpers shared by Pixel Raiders, Road Hopper and
 * City Defense: pixel sprites compiled once from text, a pixel digit font
 * (so scores look crisp before the web font loads and never need words), and
 * a fixed-size particle pool. Nothing here allocates per frame.
 */

/** A bitmap compiled to horizontal runs: (x, y, length) triplets. */
export interface PixelSprite {
  w: number;
  h: number;
  runs: Int16Array;
}

/** Compile a sprite from rows of text where `#` is a lit pixel and anything else is empty. */
export function sprite(rows: readonly string[]): PixelSprite {
  const runs: number[] = [];
  let w = 0;
  rows.forEach((row, y) => {
    w = Math.max(w, row.length);
    let start = -1;
    for (let x = 0; x <= row.length; x += 1) {
      const lit = x < row.length && row[x] === "#";
      if (lit && start < 0) start = x;
      if (!lit && start >= 0) {
        runs.push(start, y, x - start);
        start = -1;
      }
    }
  });
  return { w, h: rows.length, runs: Int16Array.from(runs) };
}

/** Paint a sprite in the current fill style with its top-left at (x, y); `flip` mirrors it horizontally. */
export function drawSprite(ctx: CanvasRenderingContext2D, s: PixelSprite, x: number, y: number, scale = 1, flip = false): void {
  const r = s.runs;
  const ox = Math.round(x), oy = Math.round(y);
  for (let i = 0; i < r.length; i += 3) {
    const rx = flip ? s.w - r[i] - r[i + 2] : r[i];
    ctx.fillRect(ox + rx * scale, oy + r[i + 1] * scale, r[i + 2] * scale, scale);
  }
}

const DIGITS: readonly PixelSprite[] = [
  ["###", "#.#", "#.#", "#.#", "###"],
  [".#.", "##.", ".#.", ".#.", "###"],
  ["###", "..#", "###", "#..", "###"],
  ["###", "..#", ".##", "..#", "###"],
  ["#.#", "#.#", "###", "..#", "..#"],
  ["###", "#..", "###", "..#", "###"],
  ["###", "#..", "###", "#.#", "###"],
  ["###", "..#", ".#.", ".#.", ".#."],
  ["###", "#.#", "###", "#.#", "###"],
  ["###", "#.#", "###", "..#", "###"],
].map(sprite);

/** Width in pixels of `value` written with `drawNumber` at `scale`. */
export function numberWidth(value: number, scale = 1): number {
  let n = Math.max(0, Math.floor(value));
  let count = 1;
  while (n >= 10) { n = Math.floor(n / 10); count += 1; }
  return (count * 4 - 1) * scale;
}

/**
 * Write a non-negative integer in the 3×5 pixel digit font, in the current
 * fill style. `align` anchors x at the number's left edge, centre or right edge.
 */
export function drawNumber(
  ctx: CanvasRenderingContext2D, value: number, x: number, y: number, scale = 1, align: "left" | "center" | "right" = "left",
): void {
  const n = Math.max(0, Math.floor(value));
  const width = numberWidth(n, scale);
  let right = align === "left" ? x + width : align === "center" ? x + width / 2 : x;
  let rest = n;
  do {
    const d = rest % 10;
    rest = Math.floor(rest / 10);
    right -= 3 * scale;
    drawSprite(ctx, DIGITS[d], right, y, scale);
    right -= scale;
  } while (rest > 0);
}

export function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/** One spark of an explosion, splash or puff; pooled, never allocated during play. */
export interface Particle {
  active: boolean;
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number;
  max: number;
  size: number;
  color: string;
}

export function makeParticles(count: number): Particle[] {
  const pool: Particle[] = [];
  for (let i = 0; i < count; i += 1) pool.push({ active: false, x: 0, y: 0, vx: 0, vy: 0, life: 0, max: 1, size: 1, color: "#fff" });
  return pool;
}

/** Throw `count` sparks out of (x, y); when the pool is full the oldest-looking slots are reused. */
export function emitBurst(
  pool: Particle[], x: number, y: number, count: number, speed: number, color: string, random: () => number, life = 0.6, size = 1,
): void {
  let cursor = 0;
  for (let n = 0; n < count; n += 1) {
    // Find a free slot; if there is none, take the one closest to dying.
    let slot = -1;
    for (; cursor < pool.length; cursor += 1) if (!pool[cursor].active) { slot = cursor; break; }
    if (slot < 0) {
      let least = Infinity;
      for (let i = 0; i < pool.length; i += 1) if (pool[i].life < least) { least = pool[i].life; slot = i; }
    }
    if (slot < 0) return;
    const p = pool[slot];
    const a = random() * Math.PI * 2;
    const v = speed * (0.3 + random() * 0.7);
    p.active = true;
    p.x = x; p.y = y;
    p.vx = Math.cos(a) * v; p.vy = Math.sin(a) * v;
    p.max = life * (0.6 + random() * 0.4);
    p.life = p.max;
    p.size = size;
    p.color = color;
  }
}

export function stepParticles(pool: Particle[], dt: number, gravity = 0): void {
  for (const p of pool) {
    if (!p.active) continue;
    p.life -= dt;
    if (p.life <= 0) { p.active = false; continue; }
    p.vy += gravity * dt;
    p.x += p.vx * dt;
    p.y += p.vy * dt;
  }
}

export function clearParticles(pool: Particle[]): void {
  for (const p of pool) p.active = false;
}

/** Sparks fade out over their life (alpha, not blinking, so this is fine under reduced motion). */
export function drawParticles(ctx: CanvasRenderingContext2D, pool: Particle[]): void {
  for (const p of pool) {
    if (!p.active) continue;
    ctx.globalAlpha = clamp(p.life / p.max, 0, 1);
    ctx.fillStyle = p.color;
    ctx.fillRect(Math.round(p.x), Math.round(p.y), p.size, p.size);
  }
  ctx.globalAlpha = 1;
}

/** Fixed star positions for a backdrop, derived from a seed so the sky never changes between frames. */
export function starField(count: number, width: number, height: number, seed: number): Float32Array {
  const stars = new Float32Array(count * 3);
  let a = seed >>> 0;
  const next = (): number => {
    a = (Math.imul(a, 1664525) + 1013904223) >>> 0;
    return a / 4294967296;
  };
  for (let i = 0; i < count; i += 1) {
    stars[i * 3] = Math.floor(next() * width);
    stars[i * 3 + 1] = Math.floor(next() * height);
    stars[i * 3 + 2] = next();
  }
  return stars;
}

/**
 * Paint a star field. Stars twinkle softly with `time` unless `reduced`, where
 * they hold a steady brightness.
 */
export function drawStars(ctx: CanvasRenderingContext2D, stars: Float32Array, time: number, reduced: boolean, color = "#cfd8ff"): void {
  ctx.fillStyle = color;
  for (let i = 0; i < stars.length; i += 3) {
    const phase = stars[i + 2];
    ctx.globalAlpha = reduced ? 0.35 + phase * 0.4 : 0.3 + 0.45 * (0.5 + 0.5 * Math.sin(time * (0.8 + phase * 1.6) + phase * 40));
    ctx.fillRect(stars[i], stars[i + 1], 1, 1);
  }
  ctx.globalAlpha = 1;
}
