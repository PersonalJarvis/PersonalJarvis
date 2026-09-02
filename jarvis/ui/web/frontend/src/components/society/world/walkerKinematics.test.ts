import { describe, expect, it } from "vitest";

import {
  ARRIVE_SLOW_M,
  arrivalFactor,
  headingFor,
  isMoving,
  lateralOffset,
  shortestArc,
  stepAlong,
  turnToward,
} from "./walkerKinematics";

describe("walkerKinematics", () => {
  it("faces where it walks: +Z is heading 0, +X is heading +90°", () => {
    expect(headingFor(0, 1)).toBeCloseTo(0);
    expect(headingFor(1, 0)).toBeCloseTo(Math.PI / 2);
    expect(headingFor(0, -1)).toBeCloseTo(Math.PI);
    expect(headingFor(-1, 0)).toBeCloseTo(-Math.PI / 2);
    expect(headingFor(1, 1)).toBeCloseTo(Math.PI / 4);
    expect(headingFor(-1, -1)).toBeCloseTo((-3 * Math.PI) / 4);
  });

  it("turns the short way round across the ±π seam", () => {
    expect(shortestArc(0.9 * Math.PI, -0.9 * Math.PI)).toBeCloseTo(0.2 * Math.PI);
    expect(shortestArc(-0.9 * Math.PI, 0.9 * Math.PI)).toBeCloseTo(-0.2 * Math.PI);
    expect(shortestArc(0, Math.PI / 2)).toBeCloseTo(Math.PI / 2);
    const next = turnToward(0.95 * Math.PI, -0.95 * Math.PI, 0.05);
    expect(shortestArc(0.95 * Math.PI, next)).toBeGreaterThan(0);
  });

  it("never exceeds the turn step and lands exactly when close", () => {
    expect(turnToward(0, 0.01, 0.1)).toBe(0.01);
    const stepped = turnToward(0, 2, 0.3);
    expect(stepped).toBeGreaterThan(0);
    expect(stepped).toBeLessThanOrEqual(0.3 + 1e-9);
  });

  it("keeps the heading frozen at rest", () => {
    expect(isMoving(0.01, 0.01)).toBe(false);
    expect(isMoving(0.1, 0)).toBe(true);
  });

  it("decelerates into the last waypoint and reaches it exactly", () => {
    expect(arrivalFactor(10)).toBe(1);
    expect(arrivalFactor(ARRIVE_SLOW_M / 2)).toBeCloseTo(0.5);
    expect(arrivalFactor(0)).toBe(0.35);
    let x = 0;
    let z = 0;
    let index = 0;
    const path: Array<[number, number]> = [
      [0, 2],
      [2, 2],
    ];
    let steps = 0;
    for (; steps < 1000; steps++) {
      const r = stepAlong(x, z, path, index, 1.0, 1 / 60);
      x = r.x;
      z = r.z;
      index = r.index;
      if (r.arrived) break;
    }
    expect(x).toBeCloseTo(2, 6);
    expect(z).toBeCloseTo(2, 6);
    expect(steps).toBeGreaterThan(4 * 60 - 5); // ≥ 4 m at ≤ 1 m/s
  });

  it("passes through corners without stopping and reports the velocity", () => {
    const r = stepAlong(0, 0, [[0, 1], [1, 1]], 0, 2.0, 1.0);
    // 2 m of budget: 1 m north to the corner, then 1 m east.
    expect(r.x).toBeCloseTo(1);
    expect(r.z).toBeCloseTo(1);
    expect(r.arrived).toBe(true);
    expect(Math.hypot(r.vx, r.vz)).toBeGreaterThan(1);
  });

  it("offsets walkers deterministically within ±0.3 m", () => {
    expect(lateralOffset("scout")).toBe(lateralOffset("scout"));
    for (const id of ["jarvis", "scout", "archivist", "quill"]) {
      expect(Math.abs(lateralOffset(id))).toBeLessThanOrEqual(0.3);
    }
  });
});
