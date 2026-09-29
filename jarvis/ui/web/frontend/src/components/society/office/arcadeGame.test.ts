import { describe, expect, it } from "vitest";
import {
  ARCADE_H, ARCADE_W, ROCK_RADIUS, arcadeLevel, newArcade, spawnInterval, speedFactor, stepArcade,
  type ArcadeInput, type ArcadeState, type Rock,
} from "./arcadeGame";

const idle: ArcadeInput = { left: false, right: false, up: false, down: false, fire: false };
const DT = 1 / 120;
const run = (s: ArcadeState, input: ArcadeInput, seconds: number, random: () => number = () => 0.5) => {
  for (let t = 0; t < seconds; t += DT) stepArcade(s, input, DT, random);
};
const rock = (over: Partial<Rock>): Rock => ({
  x: 0, y: 0, vx: 0, vy: 0, size: 0, hp: 1, angle: 0, spin: 0, shape: [1, 1, 1, 1, 1, 1], flash: 0, ...over,
});
const playing = (): ArcadeState => {
  const s = newArcade(() => 0.5);
  s.phase = "playing";
  s.spawnTimer = 999;
  return s;
};

/** Deterministic pseudo-random numbers for the balance runs. */
function seeded(seed: number): () => number {
  let x = seed;
  return () => { x = (x * 1664525 + 1013904223) % 4294967296; return x / 4294967296; };
}

describe("asteroid run", () => {
  it("does nothing until launched", () => {
    const s = newArcade();
    run(s, { ...idle, up: true }, 1);
    expect(s.time).toBe(0);
    expect(s.rocks).toHaveLength(0);
  });

  it("flies the rocket in every direction and keeps it on screen", () => {
    const s = playing();
    const y0 = s.shipY;
    run(s, { ...idle, up: true }, 0.3);
    expect(s.shipY).toBeLessThan(y0);
    run(s, { ...idle, left: true }, 5);
    expect(s.shipX).toBeGreaterThan(0);
    expect(s.shipX).toBeLessThan(15);
    run(s, { ...idle, right: true, down: true }, 5);
    expect(s.shipX).toBeLessThan(ARCADE_W);
    expect(s.shipY).toBeLessThan(ARCADE_H);
  });

  it("drifts to a stop when no key is held", () => {
    const s = playing();
    run(s, { ...idle, right: true }, 0.3);
    run(s, idle, 2);
    expect(Math.abs(s.vx)).toBeLessThan(1);
  });

  it("breaks a big rock into two smaller ones and scores it", () => {
    const s = playing();
    s.rocks = [rock({ x: 120, y: 100, size: 2, hp: 1 })];
    s.shots = [{ x: 120, y: 100 }];
    stepArcade(s, idle, 0.001, () => 0.5);
    expect(s.rocks.map((r) => r.size)).toEqual([1, 1]);
    expect(s.score).toBeGreaterThanOrEqual(25);
  });

  it("needs several hits for a large rock", () => {
    const s = playing();
    s.rocks = [rock({ x: 120, y: 100, size: 2, hp: 4 })];
    s.shots = [{ x: 120, y: 100 }];
    stepArcade(s, idle, 0.001, () => 0.5);
    expect(s.rocks).toHaveLength(1);
    expect(s.rocks[0].hp).toBe(3);
  });

  it("loses a life on a crash, blinks, and ends on the last life", () => {
    const s = playing();
    s.rocks = [rock({ x: s.shipX, y: s.shipY })];
    stepArcade(s, idle, 0.001);
    expect(s.lives).toBe(2);
    expect(s.invulnerable).toBeGreaterThan(0);
    s.rocks = [rock({ x: s.shipX, y: s.shipY })];
    stepArcade(s, idle, 0.001);
    expect(s.lives).toBe(2);
    s.invulnerable = 0;
    s.lives = 1;
    s.rocks = [rock({ x: s.shipX, y: s.shipY })];
    stepArcade(s, idle, 0.001);
    expect(s.phase).toBe("over");
  });

  it("a shield absorbs one crash", () => {
    const s = playing();
    s.shield = true;
    s.rocks = [rock({ x: s.shipX, y: s.shipY })];
    stepArcade(s, idle, 0.001);
    expect(s.lives).toBe(3);
    expect(s.shield).toBe(false);
  });

  it("collects pickups by touching them", () => {
    const s = playing();
    s.pickups = [{ x: s.shipX, y: s.shipY, kind: "rapid", t: 0 }, { x: s.shipX, y: s.shipY, kind: "life", t: 0 }];
    stepArcade(s, idle, 0.001);
    expect(s.rapid).toBeGreaterThan(0);
    expect(s.lives).toBe(4);
  });

  it("gets harder the longer you survive", () => {
    expect(spawnInterval(0)).toBeGreaterThan(1);
    expect(spawnInterval(120)).toBeLessThan(spawnInterval(30));
    expect(speedFactor(100)).toBeGreaterThan(2.5);
    const s = playing();
    s.time = 45;
    expect(arcadeLevel(s)).toBe(3);
  });

  it("starts gentle: an idle rocket survives the first seconds, a long idle run does not", () => {
    // Averaged over seeds so the check is about the curve, not one lucky field.
    let early = 0, late = 0;
    for (let seed = 1; seed <= 20; seed += 1) {
      const random = seeded(seed);
      const s = newArcade(random);
      s.phase = "playing";
      run(s, idle, 8, random);
      if (s.lives === 3) early += 1;
      run(s, idle, 170, random);
      if ((s.phase as string) === "over") late += 1;
    }
    expect(early).toBeGreaterThanOrEqual(12);
    expect(late).toBe(20);
  });

  it("keeps rocks inside the side walls", () => {
    const s = playing();
    s.rocks = [rock({ x: 3, y: 100, vx: -50, size: 1 })];
    stepArcade(s, idle, 0.05);
    expect(s.rocks[0].x).toBeGreaterThanOrEqual(ROCK_RADIUS[1]);
    expect(s.rocks[0].vx).toBeGreaterThan(0);
  });
});
