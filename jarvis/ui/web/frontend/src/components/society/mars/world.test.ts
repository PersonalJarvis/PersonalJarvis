import { describe, expect, it } from "vitest";
import { BUILDING_COLLIDERS, createTerrainGeometry, PLAYER_SPAWN, projectRoad, ROADS, surfaceHeight, terrainHeight, WORLD } from "./world";
import { isPositionClear } from "./controller";

describe("canonical Mars foundation", () => {
  it("connects every declared navigation node to the Outpost without changed endpoint heights", () => {
    const visited = new Set(["outpost-arrival"]);
    for (let pass = 0; pass < WORLD.navigation.nodes.length; pass++) {
      for (const road of ROADS) {
        if (visited.has(road.from)) visited.add(road.to);
        if (visited.has(road.to)) visited.add(road.from);
        expect(projectRoad(road.start[0], road.start[2], road).height).toBe(road.start[1]);
        expect(projectRoad(road.end[0], road.end[2], road).height).toBe(road.end[1]);
      }
    }
    expect([...visited].sort()).toEqual(WORLD.navigation.nodes.map((node) => node.id).sort());
  });

  it("keeps shared nodes within the authored/proxy deck seam tolerance", () => {
    for (const node of WORLD.navigation.nodes) {
      expect(Math.abs(surfaceHeight(node.position[0], node.position[2]) - node.position[1]), node.id).toBeLessThanOrEqual(0.081);
    }
    expect(surfaceHeight(294, 64)).toBe(58); // Finished operations floor.
    expect(surfaceHeight(365, 60)).toBeCloseTo(57.97, 5); // Authored terrace.
  });

  it("keeps rendered terrain below every road centre and avoids a raised endpoint cap", () => {
    for (const road of ROADS) {
      for (let step = 0; step <= 20; step++) {
        const t = step / 20;
        const x = road.start[0] + (road.end[0] - road.start[0]) * t;
        const z = road.start[2] + (road.end[2] - road.start[2]) * t;
        const y = road.start[1] + (road.end[1] - road.start[1]) * t;
        expect(terrainHeight(x, z), `${road.id}/${t}`).toBeLessThanOrEqual(y + 0.08);
      }
    }
    const bridge = ROADS.find((road) => road.id === "route-01")!;
    const t = 126 / 131;
    expect(surfaceHeight(251, 40 + 10 * t)).toBeCloseTo(48 + 10 * t, 5);
    expect(projectRoad(251, 40 + 10 * t, bridge).distance).toBeCloseTo(0);
  });

  it("provides a clear initial spawn and a genuine open doorway", () => {
    expect(isPositionClear(PLAYER_SPAWN)).toBe(true);
    expect(isPositionClear([294, 58.08, 71])).toBe(true);
    expect(isPositionClear([294, 58.08, 64])).toBe(true);
    expect(isPositionClear([290, 58.08, 71])).toBe(false);
    expect(BUILDING_COLLIDERS.some((collider) => collider.id === "outpost:operations-roof")).toBe(true);
  });

  it("rebuilds identical terrain including the entire original bounds", () => {
    const a = createTerrainGeometry(), b = createTerrainGeometry();
    expect(a.getAttribute("position").array).toEqual(b.getAttribute("position").array);
    expect(a.getIndex()!.count).toBeGreaterThan(170000);
    a.computeBoundingBox();
    expect(a.boundingBox!.min.y).toBe(-12);
    expect(a.boundingBox!.min.x).toBe(WORLD.bounds.min[0]);
    expect(a.boundingBox!.max.z).toBe(WORLD.bounds.max[2]);
    a.dispose(); b.dispose();
  });
});
