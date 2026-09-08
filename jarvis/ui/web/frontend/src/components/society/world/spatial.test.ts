import { describe, expect, it } from "vitest";
import { avoidsAgents, ObstacleIndex, polygonsOverlap, rectangle, sweptDiscHits } from "./spatial";

describe("swept bodies", () => {
  it("blocks a fast step even when both endpoints are outside a thin wall", () => {
    expect(sweptDiscHits([-10, 0], [10, 0], .4, rectangle(0, 0, .1, 8))).toBe(true);
  });
  it("keeps shoulders away from a corner instead of treating a walker as a point", () => {
    expect(sweptDiscHits([-2, 1.3], [2, 1.3], .4, rectangle(0, 0, 2, 2))).toBe(true);
    expect(sweptDiscHits([-2, 1.5], [2, 1.5], .4, rectangle(0, 0, 2, 2))).toBe(false);
  });
  it("indexes obstacles across negative and positive sector boundaries", () => {
    const index = new ObstacleIndex([{ id: "wall", polygon: rectangle(-32, 0, 2, 6) }]);
    expect(index.clear([-35, 0], [-29, 0], .5)).toBe(false);
  });
  it("detects crossing rotated footprints with no contained corners", () => {
    expect(polygonsOverlap(rectangle(0, 0, 10, 1), rectangle(0, 0, 10, 1, Math.PI / 2))).toBe(true);
  });
  it("prevents head-on passage and permits separating overlapping legacy starts", () => {
    const others = [{ id: "b", x: 0, z: 0, radius: .5 }];
    expect(avoidsAgents("a", [-2, 0], [2, 0], .5, others)).toBe(false);
    expect(avoidsAgents("a", [.5, 0], [.6, 0], .5, others)).toBe(true);
    expect(avoidsAgents("a", [.5, 0], [.4, 0], .5, others)).toBe(false);
  });
});
