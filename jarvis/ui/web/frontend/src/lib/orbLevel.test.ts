import { describe, expect, test } from "vitest";
import {
  driveTarget,
  flickerEnvelope,
  isOnset,
  orbDriveFor,
  pulseEnvelope,
  smoothOrbLevel,
  speechEnvelope,
  ONSET_FLOOR,
  RIPPLE_MIN_GAP_MS,
} from "@/lib/orbLevel";

/**
 * The orb's level (2026-08-19): the real microphone while listening, a
 * speech-shaped envelope while the assistant speaks, a heartbeat while it
 * thinks — one smoothed number everything on the orb reads.
 */
describe("orbLevel", () => {
  test("each voice state has its drive; idle and trouble have none", () => {
    expect(orbDriveFor("listening")).toBe("mic");
    expect(orbDriveFor("speaking")).toBe("speech");
    expect(orbDriveFor("thinking")).toBe("pulse");
    expect(orbDriveFor("connecting")).toBe("flicker");
    expect(orbDriveFor("idle")).toBe("idle");
    expect(orbDriveFor("error")).toBe("idle");
    expect(orbDriveFor("paused")).toBe("idle");
  });

  test("the microphone drives the level directly, clipped to 0..1", () => {
    expect(driveTarget("mic", 3, 0.4)).toBe(0.4);
    expect(driveTarget("mic", 3, 1.7)).toBe(1);
    expect(driveTarget("mic", 3, -1)).toBe(0);
    expect(driveTarget("mic", 3, Number.NaN)).toBe(0);
    expect(driveTarget("idle", 3, 0.9)).toBe(0);
  });

  test("attack is quick, release is soft", () => {
    const up = smoothOrbLevel(0, 1, 1 / 60);
    const down = 1 - smoothOrbLevel(1, 0, 1 / 60);
    expect(up).toBeGreaterThan(down);
    expect(up).toBeGreaterThan(0.25);
    expect(down).toBeLessThan(0.2);
    // Converges, never overshoots.
    let v = 0;
    for (let i = 0; i < 120; i++) v = smoothOrbLevel(v, 0.7, 1 / 60);
    expect(v).toBeCloseTo(0.7, 2);
    expect(smoothOrbLevel(0.5, 0.5, 1)).toBe(0.5);
  });

  test("the speech envelope swells in, moves like syllables, and stays in 0..1", () => {
    expect(speechEnvelope(0)).toBe(0);
    expect(speechEnvelope(0.18)).toBeGreaterThan(0.1);
    let lo = 1;
    let hi = 0;
    let turns = 0;
    let lastSign = 0;
    let prev = speechEnvelope(0.2);
    for (let t = 0.2; t < 4; t += 1 / 60) {
      const v = speechEnvelope(t);
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(1);
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
      const s = Math.sign(v - prev);
      if (s !== 0 && lastSign !== 0 && s !== lastSign) turns++;
      if (s !== 0) lastSign = s;
      prev = v;
    }
    // It breathes over a real range and turns around many times in 4 s — not
    // a sine, not a flat line.
    expect(hi - lo).toBeGreaterThan(0.4);
    expect(turns).toBeGreaterThan(6);
  });

  test("the heartbeat and the flicker are small, periodic and bounded", () => {
    for (let t = 0; t < 3; t += 0.05) {
      expect(pulseEnvelope(t)).toBeGreaterThanOrEqual(0);
      expect(pulseEnvelope(t)).toBeLessThanOrEqual(0.6);
      expect(flickerEnvelope(t)).toBeGreaterThanOrEqual(0.1);
      expect(flickerEnvelope(t)).toBeLessThanOrEqual(0.2);
    }
    expect(pulseEnvelope(0.07)).toBeGreaterThan(pulseEnvelope(0.6));
    expect(pulseEnvelope(0.07)).toBeCloseTo(pulseEnvelope(0.97), 2);
  });

  test("an onset is a real jump above the floor, rate-limited", () => {
    expect(isOnset(0.1, 0.4, 1000)).toBe(true);
    expect(isOnset(0.3, 0.36, 1000)).toBe(false); // too small a jump
    expect(isOnset(0.0, 0.15, 1000)).toBe(false); // under the floor
    expect(isOnset(0.1, 0.4, RIPPLE_MIN_GAP_MS - 1)).toBe(false); // too soon
    expect(isOnset(0.1, ONSET_FLOOR, RIPPLE_MIN_GAP_MS)).toBe(true);
  });
});
