import { describe, expect, it } from "vitest";
import {
  createGigiFlight, createGigiPose, GIGI_HOVER_M, GIGI_MAX_LEAN, GIGI_MAX_Y, GIGI_MIN_Y, GIGI_SLEEP_HOVER_M,
  stepGigiFlight, type GigiFlightMode,
} from "./gigiFlight";

const MODES: GigiFlightMode[] = ["idle", "work", "talk", "wave", "sleep"];
const DT = 1 / 60;

describe("gigi flight", () => {
  it("stays between 0.6 and 1.5 m in every mode, speaking or not, at any frame rate", () => {
    for (const mode of MODES) {
      for (const speaking of [false, true]) {
        for (const dt of [1 / 144, 1 / 30, 0.25]) {
          const state = createGigiFlight(0, 0);
          const pose = createGigiPose();
          for (let t = 0; t < 20; t += dt) {
            stepGigiFlight(state, { targetX: Math.sin(t) * 2, targetZ: 0, moving: t % 6 < 3, mode, speaking, t, dt }, pose);
            expect(pose.y).toBeGreaterThanOrEqual(GIGI_MIN_Y);
            expect(pose.y).toBeLessThanOrEqual(GIGI_MAX_Y);
            expect(pose.glow).toBeGreaterThanOrEqual(0);
            expect(pose.glow).toBeLessThanOrEqual(1);
            expect(Number.isFinite(pose.x + pose.z + pose.yaw + pose.pitch + pose.roll + pose.scale)).toBe(true);
          }
        }
      }
    }
  });

  it("hovers at chest height, and sinks lower to sleep", () => {
    const awake = createGigiFlight(0, 0);
    const asleep = createGigiFlight(0, 0);
    const sumAwake = { y: 0 }, sumAsleep = { y: 0 };
    let n = 0;
    for (let t = 0; t < 30; t += DT) {
      const a = stepGigiFlight(awake, { targetX: 0, targetZ: 0, moving: false, mode: "idle", speaking: false, t, dt: DT });
      const s = stepGigiFlight(asleep, { targetX: 0, targetZ: 0, moving: false, mode: "sleep", speaking: false, t, dt: DT });
      if (t > 10) { sumAwake.y += a.y; sumAsleep.y += s.y; n++; }
    }
    expect(sumAwake.y / n).toBeCloseTo(GIGI_HOVER_M, 1);
    expect(sumAsleep.y / n).toBeCloseTo(GIGI_SLEEP_HOVER_M, 1);
  });

  it("converges to a moved target without shooting more than 0.15 m past it", () => {
    for (const dt of [1 / 144, DT, 1 / 20]) {
      // A jump of 3 m (below the snap threshold) along +x.
      const state = createGigiFlight(0, 0);
      let maxPast = 0;
      for (let t = 0; t < 6; t += dt) {
        const pose = stepGigiFlight(state, { targetX: 3, targetZ: 0, moving: t < 2, mode: "work", speaking: false, t, dt });
        maxPast = Math.max(maxPast, pose.x - 3);
      }
      expect(maxPast).toBeLessThanOrEqual(0.15);
      expect(Math.abs(state.x - 3)).toBeLessThan(0.01);

      // A walker going along an L-shaped path at walking pace, then stopping.
      const walk = createGigiFlight(0, 0);
      let pastX = 0, pastZ = 0;
      for (let t = 0; t < 10; t += dt) {
        const s = Math.min(t * 1.35, 6);
        const tx = Math.min(s, 3), tz = Math.max(0, s - 3);
        const pose = stepGigiFlight(walk, { targetX: tx, targetZ: tz, moving: s < 6, mode: "work", speaking: false, t, dt });
        pastX = Math.max(pastX, pose.x - 3);
        pastZ = Math.max(pastZ, pose.z - 3);
      }
      expect(pastX).toBeLessThanOrEqual(0.15);
      expect(pastZ).toBeLessThanOrEqual(0.15);
      expect(Math.hypot(walk.x - 3, walk.z - 3)).toBeLessThan(0.01);
    }
  });

  it("snaps when the walker was re-placed far away", () => {
    const state = createGigiFlight(0, 0);
    stepGigiFlight(state, { targetX: 10, targetZ: -4, moving: false, mode: "idle", speaking: false, t: 0, dt: DT });
    expect(state.x).toBe(10);
    expect(state.z).toBe(-4);
  });

  it("faces the travel direction, leans forward and never over 15°", () => {
    for (const [dx, dz] of [[1, 0], [0, 1], [-1, 0], [0.6, -0.8]] as const) {
      const state = createGigiFlight(0, 0);
      let pose = createGigiPose();
      for (let t = 0; t < 3; t += DT) {
        pose = stepGigiFlight(state, { targetX: dx * 1.35 * t, targetZ: dz * 1.35 * t, moving: true, mode: "idle", speaking: false, t, dt: DT }, pose);
        expect(pose.pitch).toBeLessThanOrEqual(GIGI_MAX_LEAN + 1e-9);
      }
      const expected = Math.atan2(dx, dz);
      expect(Math.abs(Math.atan2(Math.sin(pose.yaw - expected), Math.cos(pose.yaw - expected)))).toBeLessThan(0.05);
      expect(pose.pitch).toBeGreaterThan(0.1);
    }
  });

  it("banks into turns", () => {
    const state = createGigiFlight(0, 0);
    let minRoll = 0, maxRoll = 0;
    // Circle counter-clockwise seen from above in x/z: yaw keeps increasing.
    for (let t = 0; t < 4; t += DT) {
      const a = t * 0.9;
      const pose = stepGigiFlight(state, { targetX: Math.sin(a) * 1.5, targetZ: Math.cos(a) * 1.5 - 1.5, moving: true, mode: "idle", speaking: false, t, dt: DT });
      minRoll = Math.min(minRoll, pose.roll); maxRoll = Math.max(maxRoll, pose.roll);
    }
    expect(minRoll).toBeLessThan(-0.02);
    expect(maxRoll).toBeLessThan(0.02);
  });

  it("glows brighter when talking or speaking than when idle", () => {
    const glowAfter = (mode: GigiFlightMode, speaking: boolean) => {
      const state = createGigiFlight(0, 0);
      let sum = 0, n = 0;
      for (let t = 0; t < 8; t += DT) {
        const pose = stepGigiFlight(state, { targetX: 0, targetZ: 0, moving: false, mode, speaking, t, dt: DT });
        if (t > 4) { sum += pose.glow; n++; }
      }
      return sum / n;
    };
    expect(glowAfter("talk", false)).toBeGreaterThan(glowAfter("idle", false) + 0.2);
    expect(glowAfter("idle", true)).toBeGreaterThan(glowAfter("idle", false) + 0.2);
    expect(glowAfter("sleep", false)).toBeLessThan(glowAfter("idle", false));
  });

  it("reduced motion hovers statically on the target", () => {
    const state = createGigiFlight(0, 0);
    const pose = createGigiPose();
    for (let t = 0; t < 3; t += DT) {
      stepGigiFlight(state, { targetX: 1, targetZ: 2, moving: false, mode: "wave", speaking: true, t, dt: DT, heading: 1, reduced: true }, pose);
      expect(pose.x).toBe(1);
      expect(pose.z).toBe(2);
      expect(pose.y).toBe(GIGI_HOVER_M);
      expect(pose.yaw).toBeCloseTo(1);
      expect(pose.pitch).toBe(0);
    }
  });

  it("is deterministic and writes into the given pose", () => {
    const run = () => {
      const state = createGigiFlight(0.5, -1, 0.3);
      const pose = createGigiPose();
      const trace: number[] = [];
      for (let i = 0; i < 600; i++) {
        const t = i * DT;
        const mode = MODES[Math.floor(t / 2) % MODES.length]!;
        const returned = stepGigiFlight(state, { targetX: Math.cos(t) * 2, targetZ: Math.sin(t * 0.7), moving: i % 200 < 120, mode, speaking: i % 150 > 100, t, dt: DT }, pose);
        expect(returned).toBe(pose);
        trace.push(pose.x, pose.y, pose.z, pose.yaw, pose.pitch, pose.roll, pose.scale, pose.glow);
      }
      return trace;
    };
    expect(run()).toEqual(run());
  });
});
