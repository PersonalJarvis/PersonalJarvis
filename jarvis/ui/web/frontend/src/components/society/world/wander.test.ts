import { describe, expect, it } from "vitest";

import {
  DWELL_MAX_S,
  DWELL_MEAN_S,
  DWELL_MIN_S,
  REST_PROBABILITY,
  dwellSeconds,
  mulberry32,
  nextBeat,
  seedFromString,
} from "./wander";

describe("wander", () => {
  it("rests on most beats", () => {
    const rng = mulberry32(7);
    let rests = 0;
    const n = 5000;
    for (let i = 0; i < n; i++) if (nextBeat(rng).kind === "rest") rests++;
    expect(rests / n).toBeGreaterThan(REST_PROBABILITY - 0.03);
    expect(rests / n).toBeLessThan(REST_PROBABILITY + 0.03);
  });

  it("dwells exponentially around the mean, inside the clamp band", () => {
    const rng = mulberry32(11);
    let sum = 0;
    const n = 4000;
    for (let i = 0; i < n; i++) {
      const d = dwellSeconds(rng);
      expect(d).toBeGreaterThanOrEqual(DWELL_MIN_S);
      expect(d).toBeLessThanOrEqual(DWELL_MAX_S);
      sum += d;
    }
    // Clamping pulls the mean a little; it must still sit near the target.
    expect(sum / n).toBeGreaterThan(DWELL_MEAN_S - 0.8);
    expect(sum / n).toBeLessThan(DWELL_MEAN_S + 0.8);
  });

  it("is repeatable per seed and different across seeds", () => {
    const a = mulberry32(seedFromString("scout"));
    const b = mulberry32(seedFromString("scout"));
    const c = mulberry32(seedFromString("archivist"));
    expect(a()).toBe(b());
    expect(a()).not.toBe(c());
  });
});
