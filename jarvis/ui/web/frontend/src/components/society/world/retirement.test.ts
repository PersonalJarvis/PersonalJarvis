/**
 * The retirement ceremony's curves and geometry (`retirement.ts`), plus the
 * one thing about the route that can actually break: whether a body can be
 * carried from the town to the mine at all (`retirementRoute.ts`).
 *
 * The animation itself is not tested here — that is pixels. What is tested is
 * everything a bug in would put a figure in the wrong place: which way the
 * two face each other, which way a body topples, where the throw lands, and
 * that the mine is reachable from the square.
 */
import { describe, expect, it } from "vitest";

import {
  BEARER_ENTRY_M,
  EXECUTION_RANGE_M,
  MINE_APPROACH_M,
  PHASE_ORDER,
  PHASE_SECONDS,
  STRETCHER_HALF_M,
  TOSS_APEX_M,
  TOSS_STANDOFF_M,
  aimBlend,
  bearerSlots,
  facingHeading,
  fallPitch,
  firingStand,
  isDistancePhase,
  muzzleFlash,
  nextPhase,
  pathLength,
  pointBackFrom,
  recoilM,
  riflePhase,
  tossArc,
  tossFade,
  tossProgress,
} from "./retirement";
import { planRetirement } from "./retirementRoute";
import { CENTER_TILE, buildIsland, tileToWorld } from "./islandLayout";

describe("the ceremony's beats", () => {
  it("runs every phase once and ends on done", () => {
    const seen: string[] = [];
    let phase = PHASE_ORDER[0];
    for (let i = 0; i < PHASE_ORDER.length + 5 && phase !== "done"; i++) {
      seen.push(phase);
      phase = nextPhase(phase);
    }
    expect(phase).toBe("done");
    expect(seen).toEqual(PHASE_ORDER.slice(0, -1));
    expect(nextPhase("done")).toBe("done");
  });

  it("gives every timed phase a duration and every distance phase none", () => {
    for (const phase of PHASE_ORDER) {
      if (phase === "done") continue;
      if (isDistancePhase(phase)) expect(PHASE_SECONDS[phase]).toBe(0);
      else expect(PHASE_SECONDS[phase]).toBeGreaterThan(0);
    }
  });
});

describe("the rifle", () => {
  it("comes up over the raise, holds through the shot and goes down again", () => {
    expect(aimBlend("march", 0)).toBe(0);
    expect(aimBlend("raise", 0)).toBe(0);
    expect(aimBlend("raise", PHASE_SECONDS.raise)).toBeCloseTo(1);
    expect(aimBlend("hold", 0.3)).toBe(1);
    expect(aimBlend("shot", 0.1)).toBe(1);
    expect(aimBlend("fall", 0.5)).toBe(1);
    expect(aimBlend("settle", 0)).toBeCloseTo(1);
    expect(aimBlend("settle", PHASE_SECONDS.settle)).toBeCloseTo(0);
    expect(aimBlend("bearers", 0)).toBe(0);
  });

  it("is only carried between the confrontation and the holster", () => {
    expect(riflePhase("march")).toBe(false);
    expect(riflePhase("confront")).toBe(true);
    expect(riflePhase("settle")).toBe(true);
    expect(riflePhase("bearers")).toBe(false);
    expect(riflePhase("toss")).toBe(false);
  });

  it("flashes and kicks only on the shot", () => {
    expect(muzzleFlash("hold", 0.1)).toBe(0);
    expect(muzzleFlash("shot", 0)).toBeCloseTo(1);
    expect(muzzleFlash("shot", PHASE_SECONDS.shot)).toBeCloseTo(0);
    expect(muzzleFlash("fall", 0)).toBe(0);
    expect(recoilM("hold", 0.1)).toBe(0);
    expect(recoilM("shot", PHASE_SECONDS.shot / 2)).toBeGreaterThan(0.1);
  });
});

describe("the fall", () => {
  it("goes over backwards and ends flat", () => {
    expect(fallPitch(0)).toBe(0);
    expect(fallPitch(1)).toBeCloseTo(-Math.PI / 2);
    // Backwards is a NEGATIVE pitch: the head lands behind the feet, away
    // from the executioner the body was facing.
    expect(fallPitch(0.5)).toBeLessThan(0);
  });

  it("accelerates rather than easing evenly", () => {
    // A quarter of the way through the topple, less than a quarter of the way
    // over — a body drops, it does not lower itself.
    expect(Math.abs(fallPitch(0.25))).toBeLessThan(Math.PI / 8);
  });

  it("never pitches past flat once it is down", () => {
    for (let u = 0.78; u <= 1; u += 0.02) {
      expect(fallPitch(u)).toBeGreaterThanOrEqual(-Math.PI / 2 - 1e-9);
    }
  });
});

describe("who faces whom", () => {
  it("stands the executioner at range, facing the body", () => {
    const stand = firingStand([0, 0], [10, 0]);
    expect(Math.hypot(stand.x, stand.z)).toBeCloseTo(EXECUTION_RANGE_M);
    // A figure faces +Z in its own space: rotation.y = θ looks at (sin θ, cos θ).
    expect(Math.sin(stand.heading)).toBeCloseTo(-1);
    expect(Math.cos(stand.heading)).toBeCloseTo(0, 5);
  });

  it("survives both figures standing on the same spot", () => {
    const stand = firingStand([4, 4], [4, 4]);
    expect(Number.isFinite(stand.x)).toBe(true);
    expect(Math.hypot(stand.x - 4, stand.z - 4)).toBeCloseTo(EXECUTION_RANGE_M);
  });

  it("turns the condemned to look straight at its executioner", () => {
    const h = facingHeading([0, 0], [0, 5]);
    expect(Math.sin(h)).toBeCloseTo(0, 5);
    expect(Math.cos(h)).toBeCloseTo(1);
  });
});

describe("routes measured backwards", () => {
  const line: Array<[number, number]> = [
    [0, 0],
    [0, 10],
    [6, 10],
  ];

  it("measures a polyline", () => {
    expect(pathLength(line)).toBeCloseTo(16);
    expect(pathLength([])).toBe(0);
    expect(pathLength([[1, 1]])).toBe(0);
  });

  it("walks back from the end and keeps the forward heading", () => {
    const p = pointBackFrom(line, 2);
    expect(p.x).toBeCloseTo(4);
    expect(p.z).toBeCloseTo(10);
    expect(Math.sin(p.heading)).toBeCloseTo(1);
  });

  it("clamps to the start when asked for more than there is", () => {
    const p = pointBackFrom(line, 999);
    expect([p.x, p.z]).toEqual([0, 0]);
  });

  it("has no opinion about an empty line", () => {
    expect(pointBackFrom([], 5).x).toBe(0);
    expect(pointBackFrom([[3, 4]], 5)).toMatchObject({ x: 3, z: 4 });
  });
});

describe("the throw", () => {
  it("winds up before it lets go", () => {
    expect(tossProgress(0).released).toBe(false);
    expect(tossProgress(PHASE_SECONDS.toss).released).toBe(true);
    expect(tossProgress(PHASE_SECONDS.toss).fly).toBeCloseTo(1);
  });

  it("arcs from the stretcher to the portal", () => {
    const from: [number, number, number] = [0, 1, 0];
    const to: [number, number, number] = [0, 2, -5];
    expect(tossArc(0, from, to, TOSS_APEX_M)).toEqual([0, 1, 0]);
    const end = tossArc(1, from, to, TOSS_APEX_M);
    expect(end[1]).toBeCloseTo(2);
    expect(end[2]).toBeCloseTo(-5);
    // Highest in the middle, and higher than either end.
    expect(tossArc(0.5, from, to, TOSS_APEX_M)[1]).toBeGreaterThan(2 + TOSS_APEX_M / 2);
  });

  it("only disappears once it is inside", () => {
    expect(tossFade(0)).toBeCloseTo(1);
    expect(tossFade(0.5)).toBeCloseTo(1);
    expect(tossFade(1)).toBeCloseTo(0);
  });
});

describe("the stretcher", () => {
  it("puts one bearer in front and one behind", () => {
    const [front, back] = bearerSlots([0, 0], 0);
    expect(front[1]).toBeCloseTo(STRETCHER_HALF_M);
    expect(back[1]).toBeCloseTo(-STRETCHER_HALF_M);
    expect(Math.hypot(front[0] - back[0], front[1] - back[1])).toBeCloseTo(STRETCHER_HALF_M * 2);
  });

  it("turns them with the carry heading", () => {
    const [front] = bearerSlots([0, 0], Math.PI / 2);
    expect(front[0]).toBeCloseTo(STRETCHER_HALF_M);
    expect(front[1]).toBeCloseTo(0, 5);
  });
});

describe("planning a retirement on the island", () => {
  // The one route failure that would strand a body forever: the mine being
  // unreachable from the town. Everything else in the plan derives from it.
  const square = tileToWorld(CENTER_TILE + 3, CENTER_TILE + 3);
  const nearby = tileToWorld(CENTER_TILE + 8, CENTER_TILE + 6);

  it("routes a body from the square to the mine's portal", () => {
    const plan = planRetirement(square, nearby);
    expect(plan).not.toBeNull();
    if (!plan) return;
    const end = plan.carryPath[plan.carryPath.length - 1];
    // The crew stops the standoff short of the mouth, on the forecourt.
    expect(Math.hypot(end[0] - plan.portal[0], end[1] - plan.portal[2])).toBeCloseTo(
      TOSS_STANDOFF_M,
      1,
    );
    expect(pathLength(plan.carryPath)).toBeGreaterThan(MINE_APPROACH_M);
  });

  it("stands the executioner at firing range and walks it there", () => {
    const plan = planRetirement(square, nearby);
    expect(plan).not.toBeNull();
    if (!plan) return;
    expect(Math.hypot(plan.stand.x - plan.body.x, plan.stand.z - plan.body.z)).toBeCloseTo(
      EXECUTION_RANGE_M,
      5,
    );
    const last = plan.leadPath[plan.leadPath.length - 1];
    expect(last).toEqual([plan.stand.x, plan.stand.z]);
  });

  it("brings the bearers on from the mine road and walks them to the body", () => {
    const plan = planRetirement(square, nearby);
    expect(plan).not.toBeNull();
    if (!plan) return;
    const entry = plan.bearerPath[0];
    const end = plan.bearerPath[plan.bearerPath.length - 1];
    expect(end).toEqual([plan.body.x, plan.body.z]);
    // They come on from up the road, not on top of the body.
    expect(Math.hypot(entry[0] - plan.body.x, entry[1] - plan.body.z)).toBeGreaterThan(
      BEARER_ENTRY_M / 3,
    );
  });

  it("keeps the body on walkable ground", () => {
    const { map } = buildIsland();
    const plan = planRetirement(square, nearby);
    expect(plan).not.toBeNull();
    if (!plan) return;
    expect(Number.isFinite(map.level[0])).toBe(true);
    expect(Number.isFinite(plan.portal[1])).toBe(true);
  });
});
