import { describe, expect, it } from "vitest";
import { BODY_SPACING, WALK_SPEED, applySeparation, clearOfBodies, separation, stepClearOfBodies, stepMover, stepMoverAvoiding, turnToward, type Mover, wrapAngle } from "./officeMotion";

describe("office motion", () => {
  it("turns the short way and clamps the step", () => {
    expect(turnToward(0, 1, 0.5)).toBeCloseTo(0.5);
    expect(turnToward(0, 1, 2)).toBeCloseTo(1);
    // From just below +π to just above -π is a tiny step across the seam.
    expect(turnToward(3.1, -3.1, 0.2)).toBeCloseTo(wrapAngle(-3.1));
    expect(turnToward(3.0, -3.0, 0.1)).toBeCloseTo(3.1);
  });

  it("walks a path to its end at the given speed and faces the travel direction", () => {
    const m: Mover = { x: 0, z: 0, heading: 0, path: [{ x: 2, z: 0 }, { x: 2, z: 3 }] };
    let total = 0;
    let arrived = false;
    let ticks = 0;
    let headingOnFirstLeg = NaN;
    while (!arrived && ticks < 1000) {
      const res = stepMover(m, WALK_SPEED, 1 / 60);
      total += res.moved;
      arrived = res.arrived;
      ticks += 1;
      if (m.x > 1 && m.x < 1.9 && m.z === 0) headingOnFirstLeg = m.heading;
    }
    expect(arrived).toBe(true);
    expect(m.x).toBeCloseTo(2);
    expect(m.z).toBeCloseTo(3);
    expect(total).toBeCloseTo(5);
    expect(ticks).toBeCloseTo(Math.ceil(5 / WALK_SPEED * 60), -1);
    // East = +x = heading π/2; south = +z = heading 0.
    expect(headingOnFirstLeg).toBeCloseTo(Math.PI / 2);
    expect(m.heading).toBeCloseTo(0);
    expect(m.path).toHaveLength(0);
  });

  it("does nothing without a path", () => {
    const m: Mover = { x: 1, z: 1, heading: 0.3, path: [] };
    expect(stepMover(m, WALK_SPEED, 0.1)).toEqual({ moved: 0, arrived: true });
    expect(m).toEqual({ x: 1, z: 1, heading: 0.3, path: [] });
  });

  it("consumes several waypoints in one large step", () => {
    const m: Mover = { x: 0, z: 0, heading: 0, path: [{ x: 0, z: 1 }, { x: 0, z: 2 }, { x: 0, z: 3 }] };
    const res = stepMover(m, 1, 2.5);
    expect(res.moved).toBeCloseTo(2.5);
    expect(res.arrived).toBe(false);
    expect(m.path).toHaveLength(1);
    expect(m.z).toBeCloseTo(2.5);
  });
});

describe("separation", () => {
  it("is zero when nobody is near and pushes away from a close body", () => {
    expect(separation({ x: 0, z: 0 }, 0, [{ x: 5, z: 5 }])).toEqual({ x: 0, z: 0 });
    const push = separation({ x: 0, z: 0 }, Math.PI / 2, [{ x: 0.3, z: 0 }]);
    expect(push.x).toBeLessThan(0);
  });
  it("adds a sidestep when someone stands straight ahead", () => {
    // Walking towards +z, someone 0.5 m ahead: the push has a sideways component.
    const push = separation({ x: 0, z: 0 }, 0, [{ x: 0, z: 0.5 }]);
    expect(Math.abs(push.x)).toBeGreaterThan(0.1);
  });
  it("only nudges onto walkable ground and reports spacing", () => {
    const m = { x: 0, z: 0, heading: 0, path: [] };
    applySeparation(m, { x: 1, z: 0 }, 0.1, () => false);
    expect(m.x).toBe(0);
    applySeparation(m, { x: 1, z: 0 }, 0.1, () => true);
    expect(m.x).toBeGreaterThan(0);
    expect(clearOfBodies({ x: 0, z: 0 }, [{ x: BODY_SPACING / 2, z: 0 }])).toBe(false);
    expect(clearOfBodies({ x: 0, z: 0 }, [{ x: BODY_SPACING * 2, z: 0 }])).toBe(true);
  });
  it("lets a figure pinned against someone step away even when others stand far off in that direction", () => {
    // Touching a colleague to the west; the rest of the office lies south-east, beyond the step.
    const from = { x: 0, z: 0 };
    const others = [{ x: -0.4, z: 0 }, { x: 5, z: 5 }, { x: 8, z: -2 }];
    expect(stepClearOfBodies({ x: 0.03, z: 0 }, from, others)).toBe(true);
    expect(stepClearOfBodies({ x: 0, z: 0.03 }, from, others)).toBe(true);
    // Never deeper into the one already touched, never into someone new.
    expect(stepClearOfBodies({ x: -0.03, z: 0 }, from, others)).toBe(false);
    expect(stepClearOfBodies({ x: 0.03, z: 0 }, from, [{ x: 0.5, z: 0 }])).toBe(false);
  });
  it("keeps two walkers crossing head-on apart", () => {
    const a = { x: 0, z: -3, heading: 0, path: [{ x: 0, z: 3 }] };
    const b = { x: 0.02, z: 3, heading: Math.PI, path: [{ x: 0.02, z: -3 }] };
    let closest = Infinity;
    for (let i = 0; i < 600; i += 1) {
      stepMoverAvoiding(a, 1.35, 1 / 60, [b], () => true);
      stepMoverAvoiding(b, 1.35, 1 / 60, [a], () => true);
      closest = Math.min(closest, Math.hypot(a.x - b.x, a.z - b.z));
    }
    expect(closest).toBeGreaterThan(0.45);
    // Both still reach their goals.
    expect(a.path).toHaveLength(0);
    expect(b.path).toHaveLength(0);
  });
});
