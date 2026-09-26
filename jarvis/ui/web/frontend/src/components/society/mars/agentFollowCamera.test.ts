import { describe, expect, it } from "vitest";
import { createAgentFollowPose } from "./agentFollowCamera";
import { box, type Vec3 } from "./world";

describe("Mars agent follow camera", () => {
  it("keeps the same offset while translating with the rendered agent", () => {
    const start: Vec3 = [10, 3, 20];
    const delta: Vec3 = [25, 4, -30];
    const moved = start.map((value, axis) => value + delta[axis]) as Vec3;
    const first = createAgentFollowPose(start, [], () => 0);
    const next = createAgentFollowPose(moved, [], () => 0);
    expect(first.target).toEqual([10, 4.4, 20]);
    expect(first.position).toEqual([16, 8.4, 28]);
    for (let axis = 0; axis < 3; axis++) {
      expect(next.target[axis] - first.target[axis]).toBeCloseTo(delta[axis]);
      expect(next.position[axis] - first.position[axis]).toBeCloseTo(delta[axis]);
    }
    expect(createAgentFollowPose(moved, [], () => 0)).toEqual(next);
    expect(start).toEqual([10, 3, 20]);
  });

  it("shortens its sightline before a building while keeping the agent targeted", () => {
    const wall = box("wall", 3, 0, 4, 8, 8, 1);
    const pose = createAgentFollowPose([0, 0, 0], [wall], () => 0);
    expect(pose.target).toEqual([0, 1.4, 0]);
    expect(pose.position[2]).toBeGreaterThan(0);
    expect(pose.position[2]).toBeLessThan(3.15);
    expect(pose.position[0]).toBeCloseTo(pose.position[2] * 6 / 8);
  });

  it("keeps the lens in front of terrain that obstructs the follow offset", () => {
    const pose = createAgentFollowPose([0, 0, 0], [], (_x, z) => z > 3 ? 10 : 0);
    expect(pose.target).toEqual([0, 1.4, 0]);
    expect(pose.position[2]).toBeGreaterThan(0);
    expect(pose.position[2]).toBeLessThanOrEqual(3);
  });
});
