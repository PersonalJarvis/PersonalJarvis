import { describe, expect, it } from "vitest";
import {
  GUN_OFFSET, IDLE_INPUT, MAX_MISSILES, PLAY_X, launchMissile, lockedRockIds, PLAY_Y, ROCK_RADIUS, arcadeLevel, newArcade, spawnInterval, speedFactor, stepArcade,
  type ArcadeInput, type ArcadeState, type Rock,
} from "./arcadeGame";

const idle: ArcadeInput = IDLE_INPUT;
const DT = 1 / 120;
const run = (s: ArcadeState, input: ArcadeInput, seconds: number, random: () => number = () => 0.5) => {
  for (let t = 0; t < seconds; t += DT) stepArcade(s, input, DT, random);
};
let rockIds = 1000;
const rock = (over: Partial<Rock>): Rock => ({
  id: rockIds++, x: 0, y: 0, z: -40, vx: 0, vy: 0, vz: 0, size: 0, hp: 1, variant: 0, rx: 0, ry: 0, sx: 0, sy: 0, tone: 0, flash: 0, ...over,
});
const playing = (): ArcadeState => {
  const s = newArcade();
  s.phase = "playing";
  s.spawnTimer = 999;
  return s;
};

/** Deterministic pseudo-random numbers for the balance runs. */
function seeded(seed: number): () => number {
  let x = seed;
  return () => { x = (x * 1664525 + 1013904223) % 4294967296; return x / 4294967296; };
}

describe("asteroid run (3D)", () => {
  it("does nothing until launched", () => {
    const s = newArcade();
    run(s, { ...idle, up: true }, 1);
    expect(s.time).toBe(0);
    expect(s.rocks).toHaveLength(0);
  });

  it("steers in every direction and stays inside its plane", () => {
    const s = playing();
    const y0 = s.shipY;
    run(s, { ...idle, up: true }, 0.2);
    expect(s.shipY).toBeGreaterThan(y0);
    run(s, { ...idle, left: true, down: true }, 5);
    expect(s.shipX).toBe(-PLAY_X);
    expect(s.shipY).toBe(-PLAY_Y);
  });

  it("drifts to a stop when no key is held", () => {
    const s = playing();
    run(s, { ...idle, right: true }, 0.3);
    run(s, idle, 2);
    expect(Math.abs(s.vx)).toBeLessThan(0.1);
  });

  it("brings rocks towards the rocket and lets them pass", () => {
    const s = playing();
    s.rocks = [rock({ x: 8, y: 4, z: -60 })];
    s.shipX = -8;
    run(s, idle, 0.5);
    expect(s.rocks[0].z).toBeGreaterThan(-60);
    run(s, idle, 5);
    expect(s.rocks).toHaveLength(0);
    expect(s.shields).toBe(3);
  });

  it("a laser splits a big rock and scores it", () => {
    const s = playing();
    s.rocks = [rock({ x: s.shipX, y: s.shipY, z: -20, size: 2, hp: 1 })];
    s.lasers = [{ x: s.shipX, y: s.shipY, z: -17 }];
    stepArcade(s, idle, DT, () => 0.5);
    expect(s.rocks.map((r) => r.size)).toEqual([1, 1]);
    expect(s.score).toBeGreaterThanOrEqual(20);
  });

  it("a fast laser never tunnels through a small rock", () => {
    const s = playing();
    s.rocks = [rock({ x: 0, y: 0, z: -30, size: 0, hp: 1 })];
    s.lasers = [{ x: 0, y: 0, z: -29 }];
    stepArcade(s, idle, 1 / 30, () => 0.5);
    expect(s.rocks).toHaveLength(0);
  });

  it("big rocks need several hits", () => {
    const s = playing();
    s.rocks = [rock({ x: 0, y: 0, z: -20, size: 2, hp: 4 })];
    s.lasers = [{ x: 0, y: 0, z: -17 }];
    stepArcade(s, idle, DT, () => 0.5);
    expect(s.rocks).toHaveLength(1);
    expect(s.rocks[0].hp).toBe(3);
  });

  it("loses a shield on a crash, blinks, and ends on the last one", () => {
    const s = playing();
    s.rocks = [rock({ x: s.shipX, y: s.shipY, z: 0 })];
    stepArcade(s, idle, DT);
    expect(s.shields).toBe(2);
    expect(s.invulnerable).toBeGreaterThan(0);
    s.rocks = [rock({ x: s.shipX, y: s.shipY, z: 0 })];
    stepArcade(s, idle, DT);
    expect(s.shields).toBe(2);
    s.invulnerable = 0;
    s.shields = 1;
    s.rocks = [rock({ x: s.shipX, y: s.shipY, z: 0 })];
    stepArcade(s, idle, DT);
    expect(s.phase).toBe("over");
  });

  it("flying through a pickup collects it", () => {
    const s = playing();
    s.pickups = [{ x: s.shipX, y: s.shipY, z: 0, kind: "shield", spin: 0 }, { x: s.shipX, y: s.shipY, z: 0, kind: "rapid", spin: 0 }];
    stepArcade(s, idle, DT);
    expect(s.shields).toBe(4);
    expect(s.rapid).toBeGreaterThan(0);
  });

  it("boost speeds the field up, drains, and refills", () => {
    const s = playing();
    stepArcade(s, idle, DT);
    const cruise = s.speed;
    run(s, { ...idle, boost: true }, 1);
    expect(s.speed).toBeGreaterThan(cruise * 1.5);
    expect(s.boost).toBeLessThan(1);
    // Held on an empty tank it only flickers back in short bursts.
    run(s, { ...idle, boost: true }, 5);
    expect(s.boost).toBeLessThan(0.25);
    run(s, idle, 3);
    expect(s.boost).toBeGreaterThan(0.3);
  });

  it("gets harder the longer you survive", () => {
    expect(spawnInterval(0)).toBeGreaterThan(0.8);
    expect(spawnInterval(120)).toBeLessThan(spawnInterval(30));
    expect(speedFactor(120)).toBe(3);
    const s = playing();
    s.time = 45;
    expect(arcadeLevel(s)).toBe(3);
  });

  it("starts gentle: an idle rocket survives the first seconds, a long idle run does not", () => {
    // Averaged over seeds so the check is about the curve, not one lucky field.
    let early = 0, late = 0;
    for (let seed = 1; seed <= 20; seed += 1) {
      const random = seeded(seed);
      const s = newArcade();
      s.phase = "playing";
      run(s, idle, 8, random);
      if (s.shields === 3) early += 1;
      run(s, idle, 200, random);
      if ((s.phase as string) === "over") late += 1;
    }
    expect(early).toBeGreaterThanOrEqual(12);
    expect(late).toBe(20);
  });

  it("fires twin laser bolts from both cannons", () => {
    const s = playing();
    stepArcade(s, { ...idle, fire: true }, DT);
    expect(s.lasers.map((l) => l.x - s.shipX).sort()).toEqual([-GUN_OFFSET, GUN_OFFSET]);
  });

  it("a clicked rock gets a homing missile that destroys it outright", () => {
    const s = playing();
    const target = rock({ x: 6, y: 3, z: -70, size: 2, hp: 4, vx: 1.5 });
    const bystander = rock({ x: -6, y: -3, z: -70, size: 2, hp: 4 });
    s.rocks = [target, bystander];
    expect(launchMissile(s, target.id)).toBe(true);
    expect(lockedRockIds(s).has(target.id)).toBe(true);
    expect(s.missileAmmo).toBe(MAX_MISSILES - 1);
    run(s, idle, 1.5);
    // Gone completely: a missile does not split a big rock into smaller ones.
    expect(s.rocks.map((r) => r.id)).toEqual([bystander.id]);
    expect(s.missiles).toHaveLength(0);
    expect(s.score).toBeGreaterThanOrEqual(20);
  });

  it("runs out of missiles and reloads them one by one", () => {
    const s = playing();
    s.rocks = [rock({ z: -140 }), rock({ z: -140 }), rock({ z: -140 }), rock({ z: -140 })];
    s.rocks.slice(0, 3).forEach((r) => expect(launchMissile(s, r.id)).toBe(true));
    expect(launchMissile(s, s.rocks[3].id)).toBe(false);
    run(s, idle, 4);
    expect(s.missileAmmo).toBe(1);
  });

  it("will not launch at a rock that is already gone", () => {
    const s = playing();
    expect(launchMissile(s, 424242)).toBe(false);
    expect(s.missileAmmo).toBe(MAX_MISSILES);
  });

  it("rock sizes grow from small to large", () => {
    expect(ROCK_RADIUS[0]).toBeLessThan(ROCK_RADIUS[1]);
    expect(ROCK_RADIUS[1]).toBeLessThan(ROCK_RADIUS[2]);
  });
});
