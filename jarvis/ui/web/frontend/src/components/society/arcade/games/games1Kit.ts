/**
 * Small shared pieces for Neon Snake, Brick Breaker and Paddle Duel: a fixed
 * spark pool (so a busy screen never allocates per step), a clamp, and the
 * recording 2D context their draw tests paint into.
 */

export function clamp(value: number, lo: number, hi: number): number {
  return value < lo ? lo : value > hi ? hi : value;
}

/** One square particle. `life` counts down to 0; a dead spark is reused. */
export interface Spark {
  x: number; y: number; vx: number; vy: number;
  life: number; max: number; size: number; color: string;
}

/** A ring of preallocated sparks: emitting overwrites the oldest one when full. */
export interface SparkPool {
  list: Spark[];
  next: number;
}

export function sparkPool(capacity: number): SparkPool {
  const list: Spark[] = [];
  for (let i = 0; i < capacity; i += 1) list.push({ x: 0, y: 0, vx: 0, vy: 0, life: 0, max: 1, size: 1, color: "#fff" });
  return { list, next: 0 };
}

/** Burst `count` sparks out of (x, y) in random directions at up to `speed` px/s. */
export function emitSparks(
  pool: SparkPool, x: number, y: number, count: number, speed: number, color: string,
  random: () => number, life = 0.5, size = 2,
): void {
  for (let i = 0; i < count; i += 1) {
    const p = pool.list[pool.next];
    pool.next = (pool.next + 1) % pool.list.length;
    const a = random() * Math.PI * 2;
    const v = speed * (0.3 + random() * 0.7);
    p.x = x; p.y = y; p.vx = Math.cos(a) * v; p.vy = Math.sin(a) * v;
    p.max = life * (0.6 + random() * 0.4); p.life = p.max; p.size = size; p.color = color;
  }
}

/** Move and age the live sparks; `drag` is the fraction of speed kept per second. */
export function stepSparks(pool: SparkPool, dt: number, gravity = 0, drag = 0.15): void {
  const keep = Math.pow(drag, dt);
  for (const p of pool.list) {
    if (p.life <= 0) continue;
    p.life -= dt;
    p.vx *= keep; p.vy = p.vy * keep + gravity * dt;
    p.x += p.vx * dt; p.y += p.vy * dt;
  }
}

export function liveSparks(pool: SparkPool): number {
  let n = 0;
  for (const p of pool.list) if (p.life > 0) n += 1;
  return n;
}

/** Paint the live sparks as fading squares; leaves globalAlpha at 1. */
export function drawSparks(ctx: CanvasRenderingContext2D, pool: SparkPool): void {
  for (const p of pool.list) {
    if (p.life <= 0) continue;
    ctx.globalAlpha = clamp(p.life / p.max, 0, 1);
    ctx.fillStyle = p.color;
    const s = p.size;
    ctx.fillRect(Math.round(p.x - s / 2), Math.round(p.y - s / 2), s, s);
  }
  ctx.globalAlpha = 1;
}

/** Faint horizontal scanlines over the whole screen, for the CRT look. */
export function drawScanlines(ctx: CanvasRenderingContext2D, width: number, height: number, alpha = 0.12): void {
  ctx.globalAlpha = alpha;
  ctx.fillStyle = "#000";
  for (let y = 1; y < height; y += 3) ctx.fillRect(0, y, width, 1);
  ctx.globalAlpha = 1;
}

/** What a draw test can check after painting into `recordingContext`. */
export interface DrawRecord {
  ctx: CanvasRenderingContext2D;
  /** Method names in call order. */
  calls: string[];
  /** Every string passed to fillText. */
  texts: string[];
  /** Numeric arguments that were NaN or infinite (a broken frame). */
  nonFinite: number;
  /** save() minus restore(); 0 after a well-behaved draw. */
  depth: number;
}

/**
 * A stand-in 2D context for tests: it implements only the methods these games
 * use, records the calls and counts bad numbers, so a draw test needs no
 * canvas package and fails loudly when a game calls something else.
 */
export function recordingContext(): DrawRecord {
  const record: DrawRecord = { ctx: null as unknown as CanvasRenderingContext2D, calls: [], texts: [], nonFinite: 0, depth: 0 };
  const call = (name: string) => (...args: unknown[]): void => {
    record.calls.push(name);
    for (const a of args) if (typeof a === "number" && !Number.isFinite(a)) record.nonFinite += 1;
  };
  const fake = {
    fillStyle: "#000" as string, strokeStyle: "#000" as string, globalAlpha: 1, lineWidth: 1,
    font: "", textAlign: "start", textBaseline: "alphabetic",
    fillRect: call("fillRect"), strokeRect: call("strokeRect"), clearRect: call("clearRect"),
    beginPath: call("beginPath"), closePath: call("closePath"), moveTo: call("moveTo"), lineTo: call("lineTo"),
    arc: call("arc"), rect: call("rect"), fill: call("fill"), stroke: call("stroke"),
    translate: call("translate"),
    save: (): void => { record.calls.push("save"); record.depth += 1; },
    restore: (): void => { record.calls.push("restore"); record.depth -= 1; },
    fillText: (text: string, x: number, y: number): void => {
      record.calls.push("fillText");
      record.texts.push(text);
      if (!Number.isFinite(x) || !Number.isFinite(y)) record.nonFinite += 1;
    },
  };
  record.ctx = fake as unknown as CanvasRenderingContext2D;
  return record;
}
