import { describe, expect, it } from "vitest";
import { BUILDING_COLLIDERS, createTerrainGeometry, outpostBounds, outpostCloseBounds, PLAYER_SPAWN, projectRoad, ROADS, surfaceHeight, terrainHeight, WORLD } from "./world";
import { isPositionClear } from "./controller";
import outpostContract from "../../../../../../../../art/studies/mars-outpost-reference/source/geometry-contract.json";

describe("canonical Mars foundation", () => {
  it("crops only the distant bridge extension from close inspection, retaining the authored subject", () => {
    const complete = outpostBounds(), close = outpostCloseBounds();
    expect(close.min[0]).toBeGreaterThan(complete.min[0]);
    expect(close.min.slice(1)).toEqual(complete.min.slice(1));
    expect(close.max).toEqual(complete.max);
    const origin = outpostContract.world_translation;
    const plateau = outpostContract.colliders.find((collider) => collider.shape === "plateau")!;
    for (const [x, z] of [...plateau.footprint!, ...outpostContract.walk_surfaces.terrace.footprint]) {
      expect(x + origin[0]).toBeGreaterThanOrEqual(close.min[0]);
      expect(x + origin[0]).toBeLessThanOrEqual(close.max[0]);
      expect(z + origin[2]).toBeGreaterThanOrEqual(close.min[2]);
      expect(z + origin[2]).toBeLessThanOrEqual(close.max[2]);
    }
    for (const collider of BUILDING_COLLIDERS.filter((item) => item.id.startsWith("outpost:"))) {
      for (let axis = 0; axis < 3; axis++) {
        expect(collider.min[axis]).toBeGreaterThanOrEqual(close.min[axis]);
        expect(collider.max[axis]).toBeLessThanOrEqual(close.max[axis]);
      }
    }
    const landing = outpostContract.anchors.find((anchor) => anchor.id === "bridge-outpost-end")!;
    const remote = outpostContract.anchors.find((anchor) => anchor.id === "bridge-colony-end")!;
    expect(landing.position[0] + origin[0]).toBeGreaterThanOrEqual(close.min[0]);
    expect(remote.position[0] + origin[0]).toBeLessThan(close.min[0]);
    expect(outpostBounds()).toEqual(complete);
  });
  it("connects every declared navigation node to the Outpost without changed endpoint heights", () => {
    const visited = new Set(["outpost-arrival"]);
    for (let pass = 0; pass < WORLD.navigation.nodes.length; pass++) {
      for (const edge of WORLD.navigation.edges) {
        if (visited.has(edge.from)) visited.add(edge.to);
        if (visited.has(edge.to)) visited.add(edge.from);
      }
    }
    expect([...visited].sort()).toEqual(WORLD.navigation.nodes.map((node) => node.id).sort());
    for (const road of ROADS) {
      expect(projectRoad(road.start[0], road.start[2], road).height).toBe(road.start[1]);
      expect(projectRoad(road.end[0], road.end[2], road).height).toBe(road.end[1]);
    }
  });

  it("keeps server solid collision identical to the complete authored runtime collision", () => {
    const canonical = WORLD.navigation.collision.solid_boxes;
    const expected = BUILDING_COLLIDERS.filter((collider) => collider.id.startsWith("outpost:"));
    expect(canonical.map((box) => box.id).sort()).toEqual(expected.map((box) => box.id).sort());
    for (const collider of expected) {
      const projected = canonical.find((box) => box.id === collider.id)!;
      for (let axis = 0; axis < 3; axis++) {
        expect(projected.min[axis], `${collider.id}/min/${axis}`).toBeCloseTo(collider.min[axis], 10);
        expect(projected.max[axis], `${collider.id}/max/${axis}`).toBeCloseTo(collider.max[axis], 10);
      }
    }
  });

  it("keeps docks, boarding and both exits on the actual authored deck without creating new roads", () => {
    expect(ROADS.map((road) => road.id)).toEqual(WORLD.navigation.surface_edges.map((edge) => edge.id));
    expect(ROADS.some((road) => road.from.startsWith("rover-") || road.to.startsWith("rover-"))).toBe(false);
    const support = WORLD.navigation.collision.support_surfaces.find((row) => row.id === "outpost:route-05")!;
    const inside = (x: number, z: number) => support.polygon.every(([ax, az], i, polygon) => {
      const [bx, bz] = polygon[(i + 1) % polygon.length];
      return (bx - ax) * (z - az) - (bz - az) * (x - ax) >= -1e-7;
    });
    for (const dock of WORLD.navigation.rover_docks) {
      const ids = [dock.node_id, ...[dock.boarding_station_id, ...dock.exit_station_ids].map((id) => WORLD.navigation.destinations.find((row) => row.id === id)!.anchor)];
      for (const id of ids) {
        const [x, y, z] = WORLD.navigation.nodes.find((node) => node.id === id)!.position;
        expect(inside(x, z), id).toBe(true);
        const projectedHeight = support.plane[0] * x + support.plane[1] * z + support.plane[2];
        expect(projectedHeight, id).toBeCloseTo(58.006, 8);
        expect(surfaceHeight(x, z), id).toBeCloseTo(projectedHeight, 8);
        expect(Math.abs(y - projectedHeight), id).toBeLessThan(0.081);
        expect(isPositionClear([x, projectedHeight, z]), id).toBe(true);
      }
    }
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
