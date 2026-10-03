import { describe, expect, it } from "vitest";
import { RETRO_STEP, idleInput, seededRandom, type RetroInput } from "../retroGame";
import desertDash, {
  AIRTIME, DUCK_HIT, JUMP_APEX, KINDS, MAX_SPEED, RUNNER_X, STAND_HIT, START_SPEED,
  clearable, collides, createDesert, jumpWindow, minGap, speedAt, spawnObstacle, type DesertState, type Obstacle,
} from "./desertDash";

/** A 2D context stand-in: every call is a no-op, texts are recorded, non-finite numbers are flagged. */
function fakeContext(texts: string[], bad: string[]): CanvasRenderingContext2D {
  const target: Record<string, unknown> = {
    fillText: (s: unknown) => { texts.push(String(s)); },
    createLinearGradient: () => ({ addColorStop: () => undefined }),
    createRadialGradient: () => ({ addColorStop: () => undefined }),
    measureText: (s: string) => ({ width: s.length * 6 }),
  };
  return new Proxy(target, {
    get: (t, key) => {
      if (typeof key === "string" && key in t) return t[key];
      return (...args: unknown[]) => {
        if (args.some((a) => typeof a === "number" && !Number.isFinite(a))) bad.push(String(key));
      };
    },
    set: (t, key, value) => { t[String(key)] = value; return true; },
  }) as unknown as CanvasRenderingContext2D;
}

const random = seededRandom(3);
/** A run with no obstacles coming for a long while. */
const clearRun = (): DesertState => {
  const s = createDesert(random);
  s.nextGap = 1e9;
  return s;
};
const jumpInput = (held: boolean, pressed: boolean): RetroInput => {
  const input = idleInput();
  input.a = held;
  input.pressed.a = pressed;
  return input;
};
/** Fly a jump: press, hold for `holdSteps`, then let go; returns the peak height and the steps in the air. */
const jump = (s: DesertState, holdSteps: number, dive = false): { peak: number; steps: number } => {
  let peak = 0, steps = 0;
  desertDash.step(s, jumpInput(true, true), RETRO_STEP, random);
  while (!s.onGround && steps < 600) {
    steps += 1;
    const input = jumpInput(steps < holdSteps, false);
    input.down = dive && s.vy < 0;
    desertDash.step(s, input, RETRO_STEP, random);
    peak = Math.max(peak, s.y);
  }
  return { peak, steps };
};

describe("desert dash: moves", () => {
  it("starts on the ground at the start speed with nothing in the way", () => {
    const s = desertDash.create(random);
    expect(s.onGround).toBe(true);
    expect(s.speed).toBe(START_SPEED);
    expect(s.obstacles.some((o) => o.active)).toBe(false);
    expect(desertDash.status(s)).toEqual({ score: 0, over: false });
  });

  it("jumps higher the longer the button is held", () => {
    const full = jump(clearRun(), 999);
    const hop = jump(clearRun(), 1);
    const mid = jump(clearRun(), 8);
    expect(full.peak).toBeCloseTo(JUMP_APEX, 0);
    expect(full.peak).toBeGreaterThan(60);
    expect(hop.peak).toBeLessThan(full.peak * 0.45);
    expect(mid.peak).toBeGreaterThan(hop.peak);
    expect(mid.peak).toBeLessThan(full.peak);
    expect(full.steps * RETRO_STEP).toBeCloseTo(AIRTIME, 1);
  });

  it("cannot jump again in mid-air, but a press just before landing is kept", () => {
    const s = clearRun();
    desertDash.step(s, jumpInput(true, true), RETRO_STEP, random);
    for (let i = 0; i < 10; i += 1) desertDash.step(s, jumpInput(true, false), RETRO_STEP, random);
    const vy = s.vy;
    desertDash.step(s, jumpInput(true, true), RETRO_STEP, random);
    expect(s.vy).toBeLessThan(vy);
    // Fall until just above the ground, press, and the jump comes on landing.
    while (s.y > 4 || s.vy > 0) desertDash.step(s, idleInput(), RETRO_STEP, random);
    desertDash.step(s, jumpInput(true, true), RETRO_STEP, random);
    let relaunched = false;
    for (let i = 0; i < 6; i += 1) {
      desertDash.step(s, jumpInput(true, false), RETRO_STEP, random);
      if (s.vy > 0) relaunched = true;
    }
    expect(relaunched).toBe(true);
  });

  it("ducks on the ground and dives in the air", () => {
    const s = clearRun();
    const down = idleInput();
    down.down = true;
    desertDash.step(s, down, RETRO_STEP, random);
    expect(s.ducking).toBe(true);
    desertDash.step(s, idleInput(), RETRO_STEP, random);
    expect(s.ducking).toBe(false);
    const normal = jump(clearRun(), 999);
    const dived = jump(clearRun(), 999, true);
    expect(dived.steps).toBeLessThan(normal.steps);
  });
});

describe("desert dash: obstacles", () => {
  const at = (kind: number, x: number): Obstacle => ({ active: true, kind, x });

  it("ends the run on hitting a cactus", () => {
    const s = clearRun();
    s.obstacles[0] = at(0, RUNNER_X + 6);
    desertDash.step(s, idleInput(), RETRO_STEP, random);
    expect(s.over).toBe(true);
    expect(desertDash.status(s).over).toBe(true);
    const d = s.distance;
    desertDash.step(s, jumpInput(true, true), RETRO_STEP, random);
    expect(s.distance).toBe(d);
  });

  it("a high bird hits a standing fox but flies over a ducking one; a low bird must be jumped", () => {
    const high = KINDS.findIndex((k) => k.bird && k.duck);
    const low = KINDS.findIndex((k) => k.bird && !k.duck);
    const s = clearRun();
    expect(collides(s, at(high, RUNNER_X))).toBe(true);
    s.ducking = true;
    expect(collides(s, at(high, RUNNER_X))).toBe(false);
    expect(collides(s, at(low, RUNNER_X))).toBe(true);
    s.ducking = false;
    s.y = KINDS[low].top + 1;
    expect(collides(s, at(low, RUNNER_X))).toBe(false);
  });

  it("every kind is clearable at every speed, and the window math matches the hitboxes", () => {
    for (let v = START_SPEED; v <= MAX_SPEED; v += 10) {
      for (const k of KINDS) expect(clearable(k, v), `kind ${KINDS.indexOf(k)} at ${v}`).toBe(true);
    }
    for (const k of KINDS) if (!k.duck) expect(jumpWindow(k.top)).not.toBeNull();
    expect(DUCK_HIT.h).toBeLessThan(STAND_HIT.h);
    expect(jumpWindow(JUMP_APEX + 1)).toBeNull();
  });

  it("always leaves a whole jump plus reaction time between obstacles", () => {
    for (const v of [START_SPEED, 220, 300, 380, MAX_SPEED]) {
      const s = createDesert(seededRandom(v));
      s.speed = v;
      s.score = 100000; // every kind unlocked
      for (let i = 0; i < 300; i += 1) {
        const gap = spawnObstacle(s, random);
        expect(gap).toBeGreaterThanOrEqual(minGap(v));
        // The time from one obstacle's front to the next is at least a jump plus a moment to react.
        expect(gap / v).toBeGreaterThan(AIRTIME);
        for (const o of s.obstacles) o.active = false;
      }
    }
  });

  it("speeds up over time, up to a cap", () => {
    expect(speedAt(0)).toBe(START_SPEED);
    expect(speedAt(10)).toBeGreaterThan(speedAt(5));
    expect(speedAt(10_000)).toBe(MAX_SPEED);
    const s = clearRun();
    for (let i = 0; i < 600; i += 1) desertDash.step(s, idleInput(), RETRO_STEP, random);
    expect(s.speed).toBeGreaterThan(START_SPEED);
    expect(s.score).toBeGreaterThan(0);
  });

  it("a simple bot that jumps and ducks on time survives long runs at every speed", () => {
    for (const seed of [1, 2, 3]) {
      const r = seededRandom(seed);
      const s = createDesert(r);
      let birds = 0;
      for (let i = 0; i < 150 / RETRO_STEP && !s.over; i += 1) {
        desertDash.step(s, bot(s), RETRO_STEP, r);
        for (const o of s.obstacles) if (o.active && KINDS[o.kind].bird && o.x > 318) birds += 1;
      }
      expect(s.over, `seed ${seed} crashed at speed ${s.speed.toFixed(0)}, score ${s.score}`).toBe(false);
      expect(s.speed).toBe(MAX_SPEED);
      expect(birds).toBeGreaterThan(0);
    }
  });

  it("draws every state without throwing, without words and without bad numbers", () => {
    const texts: string[] = [];
    const bad: string[] = [];
    const ctx = fakeContext(texts, bad);
    const s = desertDash.create(random);
    desertDash.draw(ctx, s, { time: 0, reduced: false, idle: true });
    desertDash.draw(ctx, s, { time: 2, reduced: true, idle: true });
    s.obstacles[0] = at(1, 200);
    s.obstacles[1] = at(5, 260);
    s.obstacles[2] = at(4, 120);
    s.ticks = 5;
    for (const t of [0, 40, 50, 66, 90, 110, 125, 149, 400]) {
      s.time = t;
      s.distance = t * 300;
      desertDash.draw(ctx, s, { time: t, reduced: false, idle: false });
      desertDash.draw(ctx, s, { time: t, reduced: true, idle: false });
    }
    s.ducking = true;
    s.milestoneGlow = 0.5;
    desertDash.draw(ctx, s, { time: 1, reduced: false, idle: false });
    s.over = true;
    desertDash.draw(ctx, s, { time: 1, reduced: false, idle: false });
    expect(bad).toEqual([]);
    expect(texts.length).toBeGreaterThan(0);
    for (const text of texts) expect(text).not.toMatch(/[A-Za-z]/);
  });
});

/** Jump each cactus or low bird at the last moment that clears it; duck under high birds. */
function bot(s: DesertState): RetroInput {
  const input = idleInput();
  let next: Obstacle | null = null;
  for (const o of s.obstacles) {
    if (!o.active || o.x + KINDS[o.kind].hx1 <= RUNNER_X + DUCK_HIT.x0) continue;
    if (!next || o.x < next.x) next = o;
  }
  if (!s.onGround) { input.a = s.jumpHeld; }
  if (!next) return input;
  const k = KINDS[next.kind];
  if (k.duck) {
    if (next.x + k.hx0 - (RUNNER_X + DUCK_HIT.x1) < s.speed * 0.3) input.down = true;
    return input;
  }
  if (!s.onGround) return input;
  const win = jumpWindow(k.top)!;
  const enter = (next.x + k.hx0 - (RUNNER_X + STAND_HIT.x1)) / s.speed;
  if (enter <= win.rise + RETRO_STEP) { input.a = true; input.pressed.a = true; }
  return input;
}
