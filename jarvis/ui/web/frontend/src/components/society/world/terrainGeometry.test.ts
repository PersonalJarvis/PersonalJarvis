import { describe, expect, it } from "vitest";

import { CENTER_TILE, LEVEL_Y, buildIsland, tileIndex, TileKind } from "./islandLayout";
import { buildTerrainGeometry, triangleCount } from "./terrainGeometry";

describe("terrainGeometry", () => {
  it("builds one coloured mesh with a top quad per land tile plus cliff sides", () => {
    const { map } = buildIsland();
    const geo = buildTerrainGeometry(map);
    let land = 0;
    for (let i = 0; i < map.kind.length; i++) if (map.kind[i] !== TileKind.water) land++;
    const tris = triangleCount(geo);
    expect(tris).toBeGreaterThanOrEqual(land * 2);
    // Sides exist (coast, terraces, cliffs) but stay a fraction of the tops.
    expect(tris).toBeLessThan(land * 2 * 2.5);
    expect(geo.getAttribute("color").itemSize).toBe(3);
    expect(geo.getAttribute("normal").count).toBe(geo.getAttribute("position").count);
    expect(geo.boundingSphere).not.toBeNull();
  });

  it("keeps every top vertex on a level height", () => {
    const { map } = buildIsland();
    const geo = buildTerrainGeometry(map);
    const pos = geo.getAttribute("position");
    const nor = geo.getAttribute("normal");
    const heights = new Set<number>();
    for (let v = 0; v < pos.count; v++) {
      if (nor.getY(v) === 1) heights.add(Math.round(pos.getY(v) * 100) / 100);
    }
    // Levels 1..9 plus the dock: never a stray height.
    expect(heights.size).toBeLessThanOrEqual(LEVEL_Y.length);
    const i = tileIndex(map, CENTER_TILE, CENTER_TILE);
    expect(map.kind[i]).toBe(TileKind.plaza);
  });
});
