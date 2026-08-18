/**
 * New data must not explode the map: pages keep their place, newcomers land
 * next to the pages they link to.
 */
import { describe, expect, it } from "vitest";

import { carryOverPositions, type ContinuityNode } from "@/lib/graphContinuity";

describe("carryOverPositions", () => {
  it("keeps every page that was already placed exactly where it was", () => {
    const previous: ContinuityNode[] = [
      { id: "me", x: 1, y: 2, z: 3, vx: 0.1, vy: 0, vz: -0.1, __lively: { x: 0, y: 4, z: 0 } },
      { id: "a", x: 80, y: 0, z: -20 },
    ];
    const next: ContinuityNode[] = [{ id: "me" }, { id: "a" }];
    const result = carryOverPositions(previous, next, []);
    expect(result).toEqual({ kept: 2, seated: 0 });
    expect(next[0]).toMatchObject({ x: 1, y: 2, z: 3, vx: 0.1, vy: 0, vz: -0.1, __lively: { x: 0, y: 4, z: 0 } });
    expect(next[1]).toMatchObject({ x: 80, y: 0, z: -20 });
  });

  it("seats a newcomer between the pages it links to, not at the origin", () => {
    const previous: ContinuityNode[] = [
      { id: "a", x: 100, y: 0, z: 0 },
      { id: "b", x: 100, y: 0, z: 100 },
    ];
    const next: ContinuityNode[] = [{ id: "a" }, { id: "b" }, { id: "new" }];
    const result = carryOverPositions(previous, next, [
      { source: "new", target: "a" },
      { source: "b", target: "new" },
    ]);
    expect(result).toEqual({ kept: 2, seated: 1 });
    const seated = next[2];
    // The middle of a and b is (100, 0, 50); a small scatter on top.
    expect(Math.abs(seated.x! - 100)).toBeLessThan(20);
    expect(Math.abs(seated.z! - 50)).toBeLessThan(20);
    expect(seated.vx).toBe(0);
  });

  it("seats an unlinked newcomer near the fallback point", () => {
    const next: ContinuityNode[] = [{ id: "x" }, { id: "lonely" }];
    carryOverPositions([{ id: "x", x: 500, y: 0, z: 0 }], next, [], { x: 10, y: 20, z: 30 });
    next.shift();
    expect(Math.abs(next[0].x! - 10)).toBeLessThan(20);
    expect(Math.abs(next[0].y! - 20)).toBeLessThan(20);
    expect(Math.abs(next[0].z! - 30)).toBeLessThan(20);
  });

  it("leaves a page that already carries a position alone", () => {
    const next: ContinuityNode[] = [{ id: "a", x: 7, y: 8, z: 9 }];
    const result = carryOverPositions([{ id: "b", x: 0, y: 0, z: 0 }], next, []);
    expect(result).toEqual({ kept: 0, seated: 0 });
    expect(next[0]).toMatchObject({ x: 7, y: 8, z: 9 });
  });

  it("does nothing on the first generation, so the layout still spreads it", () => {
    const next: ContinuityNode[] = [{ id: "a" }, { id: "b" }];
    const result = carryOverPositions([], next, [{ source: "a", target: "b" }]);
    expect(result).toEqual({ kept: 0, seated: 0 });
    expect(next[0].x).toBeUndefined();
  });
});
