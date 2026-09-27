import { describe, expect, it } from "vitest";
import { constrainOrbitPose, orbitLensIsClear } from "./orbitCamera";
import { frameInspectionBounds } from "./camera";
import { box, outpostCloseBounds, type Vec3 } from "./world";

describe("orbit lens motion", () => {
  it("keeps the first real Outpost wheel gesture near the previous camera instead of collapsing onto its buried pivot", () => {
    const before = frameInspectionBounds(outpostCloseBounds(), 1745 / 786);
    const requested = { target: before.target, position: before.position.map((n, i) => before.target[i] + (n - before.target[i]) * 0.94) as Vec3 };
    const result = constrainOrbitPose(before, requested);
    expect(Math.hypot(...result.position.map((n, i) => n - before.target[i]))).toBeGreaterThan(200);
    expect(result.position).toEqual(requested.position);
    expect(result.target).toEqual(before.target);
  });

  it("does not treat a subject between the pivot and a clear lens as a collision", () => {
    const subject = box("subject", 0, 0, 0, 10, 30, 10);
    const before = { position: [0, 10, 25] as Vec3, target: [0, 10, 0] as Vec3 };
    const wanted = { position: [0, 10, 20] as Vec3, target: before.target };
    expect(constrainOrbitPose(before, wanted, [subject], () => -10)).toEqual(wanted);
  });

  it("stops a large dolly before a wall and allows an immediate reverse gesture", () => {
    const wall = box("wall", 0, 0, 0, 10, 20, 1);
    const before = { position: [0, 5, 5] as Vec3, target: [0, 5, -5] as Vec3 };
    const blocked = constrainOrbitPose(before, { ...before, position: [0, 5, -4] }, [wall], () => -10);
    expect(blocked.position[2]).toBeGreaterThan(0.85);
    expect(blocked.position[2]).toBeLessThan(0.86);
    expect(constrainOrbitPose(blocked, before, [wall], () => -10)).toEqual(before);
  });

  it("moves lens and pivot together when a pan meets an obstacle", () => {
    const wall = box("wall", 5, 0, 0, 1, 20, 20);
    const before = { position: [0, 5, 5] as Vec3, target: [0, 5, 0] as Vec3 };
    const result = constrainOrbitPose(before, { position: [10, 5, 5], target: [10, 5, 0] }, [wall], () => -10);
    expect(result.position[0]).toBeLessThan(4.15);
    expect(result.position[0]).toBe(result.target[0]);
    expect(result.position[2] - result.target[2]).toBe(5);
  });

  it("stops above ground without moving the pivot into the floor", () => {
    const before = { position: [0, 10, 10] as Vec3, target: [0, 0, 0] as Vec3 };
    const result = constrainOrbitPose(before, { ...before, position: [0, -10, 10] }, [], () => 0);
    expect(result.position[1]).toBeGreaterThanOrEqual(0.35);
    expect(result.position[1]).toBeLessThan(0.351);
    expect(orbitLensIsClear([0, -1, 10], [], () => 0)).toBe(false);
  });
});
