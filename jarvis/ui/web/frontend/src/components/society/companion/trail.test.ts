import { describe, expect, it } from "vitest";
import { advancePetTrail, createPetTrail, petDisplayPosition, recordOwner } from "./trail";

describe("footstep followers", () => {
  it("stops at its chosen gap and does not overlap a stationary owner", () => {
    const trail = createPetTrail([0, 0, 0]); recordOwner(trail, [4, 0, 0]);
    for (let i = 0; i < 120; i++) advancePetTrail(trail, 1, 1 / 60);
    expect(trail.position[0]).toBeCloseTo(3, 5);
    expect(trail.speed).toBe(0);
  });
  it("walks through the recorded corner instead of cutting through a building", () => {
    const trail = createPetTrail([0, 0, 0]);
    recordOwner(trail, [3, 0, 0]); recordOwner(trail, [3, 0, 3]);
    for (let i = 0; i < 180; i++) {
      advancePetTrail(trail, 0.5, 1 / 60);
      expect(trail.position[2] === 0 || Math.abs(trail.position[0] - 3) < 1e-6).toBe(true);
    }
    expect(trail.position).toEqual([3, 0, 2.5]);
  });
  it("inserts the verified route node when network snapshots cross a corner", () => {
    const trail = createPetTrail([0, 0, 0]); recordOwner(trail, [3, 1, 3], [3, 1, 0]);
    for (let i = 0; i < 180; i++) advancePetTrail(trail, 1, 1 / 60);
    expect(trail.position[1]).toBeCloseTo(1); expect(trail.position[2]).toBeCloseTo(2);
  });
  it("bounds background-tab catch-up and re-places discontinuities without sweeping the map", () => {
    const trail = createPetTrail([0, 0, 0]); recordOwner(trail, [5, 0, 0]); advancePetTrail(trail, 1, 600);
    expect(trail.position[0]).toBeLessThanOrEqual(0.8);
    expect(recordOwner(trail, [200, 10, 200])).toBe(false);
    expect(trail.points).toEqual([]); expect(trail.position).toEqual([200, 10, 200]);
  });
  it("rejects non-finite inputs and bounds the stored history", () => {
    const trail = createPetTrail([0, 0, 0]); expect(recordOwner(trail, [NaN, 0, 0])).toBe(false);
    for (let i = 0; i < 2000; i++) recordOwner(trail, [i * 0.1, 0, 0]);
    expect(trail.points.length).toBeLessThanOrEqual(513);
  });
  it("uses a visible shoulder only when the map proves clearance", () => {
    expect(petDisplayPosition([0, 0, 0], 0, 0.5, () => true)).toEqual([0.35, 0, 0]);
    expect(petDisplayPosition([0, 0, 0], 0, 0.5, p => p[0] < 0)).toEqual([-0.35, 0, 0]);
    expect(petDisplayPosition([0, 0, 0], 0, 0.5, () => false)).toEqual([0, 0, 0]);
    expect(petDisplayPosition([0, 0, 0], 0, 0.5)).toEqual([0, 0, 0]);
  });
});
