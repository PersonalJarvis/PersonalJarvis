import { describe, expect, it } from "vitest";
import { stepMover, turnToward, WALK_SPEED, wrapAngle, type Mover } from "./officeMotion";

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
