import { afterEach, describe, expect, it } from "vitest";
import { agentPositions, bodiesExcept } from "./walkerRegistry";

describe("bodiesExcept", () => {
  afterEach(() => agentPositions.clear());

  it("counts every agent, the hovering Gigi included, but never the caller itself", () => {
    agentPositions.set("gigi", { x: 1, z: 1 });
    agentPositions.set("walker", { x: 2, z: 2 });
    expect(Array.from(bodiesExcept(null, null))).toEqual([{ x: 1, z: 1 }, { x: 2, z: 2 }]);
    expect(Array.from(bodiesExcept("walker", { x: 5, z: 5 }))).toEqual([{ x: 1, z: 1 }, { x: 5, z: 5 }]);
  });
});
