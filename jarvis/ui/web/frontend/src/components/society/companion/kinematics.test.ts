import { describe, expect, it } from "vitest";
import { advanceMotion, clearAnchor, createMotion, expressionFor, followAnchor, planLocalRoute, positionClear, recoverAt, routeClear, type CompanionWorld, type Point } from "./kinematics";

const flat: CompanionWorld = { colliders: [], getGround: () => 0 };
function simulate(hz: number) {
  const state = createMotion();
  recoverAt(state, [0, 1.15, 0], flat);
  for (let i = 0; i < hz; i++) advanceMotion(state, [4, 1.15, 0], flat, 1 / hz, [0, 0, 0]);
  return state;
}
describe("companion physical presentation", () => {
  it("finds a verifiable route around a wall instead of moving through it", () => {
    const world: CompanionWorld = { ...flat, colliders: [{ min: [1, 0, -1], max: [1.1, 3, 1] }] };
    const path = planLocalRoute([0, 1.15, 0], [3, 1.15, 0], world);
    expect(path.length).toBeGreaterThan(1);
    let previous: Point = [0, 1.15, 0];
    for (const point of path) { expect(routeClear(previous, point, world)).toBe(true); previous = point; }
    expect(previous).toEqual([3, 1.15, 0]);
  });
  it("lowers the companion below a ceiling and rejects an untraversable enclosure", () => {
    const world: CompanionWorld = { ...flat, colliders: [{ min: [-2, 1.4, -2], max: [2, 2, 2] }] };
    const anchor: Point = [0, 1.15, 0];
    expect(clearAnchor(anchor, world)).toBe(true);
    expect(anchor[1]).toBeLessThan(0.98);
    expect(planLocalRoute([0, 0.5, 0], [0, 2.5, 0], { ...world, isLoaded: p => Math.abs(p[0]) < 1 && Math.abs(p[2]) < 1 })).toEqual([]);
  });
  it("converges consistently at 30, 60 and 120 fps", () => {
    expect(simulate(30).position[0]).toBeCloseTo(simulate(60).position[0], 5);
    expect(simulate(120).position[0]).toBeCloseTo(simulate(60).position[0], 2);
  });
  it("cannot tunnel through a thin closed wall even after a long tab suspension", () => {
    const world = { ...flat, colliders: [{ min: [0.5, 0, -1] as Point, max: [0.501, 3, 1] as Point }] };
    const state = createMotion();
    recoverAt(state, [0, 1.15, 0], world);
    for (let i = 0; i < 100; i++) advanceMotion(state, [4, 1.15, 0], world, 300, [0, 0, 0]);
    expect(state.position[0]).toBeLessThanOrEqual(0.27);
    expect(state.blocked).toBe(true);
  });
  it("accounts for head clearance at low ceilings", () => {
    const world: CompanionWorld = { ...flat, colliders: [{ min: [-1, 1.4, -1], max: [1, 1.5, 1] }] };
    expect(positionClear([0, 1.15, 0], world)).toBe(false);
    expect(positionClear([0, 0.8, 0], world)).toBe(true);
  });
  it("never repositions into unloaded, underground, or invalid space", () => {
    const state = createMotion();
    expect(recoverAt(state, [1, 1, 1], { ...flat, isLoaded: () => false })).toBe(false);
    expect(recoverAt(state, [1, -1, 1], flat)).toBe(false);
    expect(recoverAt(state, [NaN, 1, 1], flat)).toBe(false);
    expect(state.initialized).toBe(false);
  });
  it("retains its player side without a camera input", () => {
    expect(followAnchor({ position: [0, 0, 0], yaw: 0 }, [0, 0, 0])).toEqual([0.95, 1.15, 0.35]);
  });
  it("does not turn stale audio into speaking after mute", () => {
    expect(expressionFor({ audio: "speaking", task: "idle", muted: true })).toBe("idle");
    expect(expressionFor({ audio: "idle", task: "error", muted: false })).toBe("error");
    expect(expressionFor({ audio: "speaking", task: "approval", muted: false })).toBe("approval");
  });
});
