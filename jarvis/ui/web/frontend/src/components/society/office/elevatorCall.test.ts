import { describe, expect, it } from "vitest";
import { atElevator, CALL_BUTTON_LOCAL, callButtonPose } from "./elevatorCall";
import { buildOfficeLayout } from "./officeLayout";

describe("callButtonPose", () => {
  it("keeps the local offset for an unrotated prop", () => {
    const pose = callButtonPose({ x: 2, z: 3, rotationY: 0 });
    expect(pose.x).toBeCloseTo(2 + CALL_BUTTON_LOCAL.x);
    expect(pose.z).toBeCloseTo(3 + CALL_BUTTON_LOCAL.z);
    expect(pose.y).toBe(CALL_BUTTON_LOCAL.y);
  });

  it("turns the offset with the prop (doors facing +x)", () => {
    const pose = callButtonPose({ x: 0, z: 0, rotationY: Math.PI / 2 });
    // Local +z (out of the doors) becomes world +x; local +x (right of the doors) becomes world -z.
    expect(pose.x).toBeCloseTo(CALL_BUTTON_LOCAL.z);
    expect(pose.z).toBeCloseTo(-CALL_BUTTON_LOCAL.x);
    expect(pose.rotationY).toBeCloseTo(Math.PI / 2);
  });

  it("sits within reach of the elevator checkpoint on both floors", () => {
    for (const variant of ["agents", "coding"] as const) {
      const layout = buildOfficeLayout([], { variant });
      const shaft = layout.furniture.find((f) => f.kind === "elevator");
      const cp = layout.checkpoints.find((c) => c.id === "elevator");
      expect(shaft && cp).toBeTruthy();
      const pose = callButtonPose(shaft!);
      expect(Math.hypot(pose.x - cp!.x, pose.z - cp!.z)).toBeLessThan(2.2);
    }
  });
});

describe("atElevator", () => {
  const cp = { x: 0, z: 0, radius: 1.2 };
  it("is true inside the checkpoint radius only", () => {
    expect(atElevator({ x: 0.5, z: 0.5 }, cp)).toBe(true);
    expect(atElevator({ x: 3, z: 0 }, cp)).toBe(false);
  });
  it("is false on a plan without an elevator", () => {
    expect(atElevator({ x: 0, z: 0 }, undefined)).toBe(false);
  });
});
