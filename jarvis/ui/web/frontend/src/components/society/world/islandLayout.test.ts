import { beforeAll, describe, expect, it } from "vitest";

import {
  CENTER_TILE,
  DOCK_TILES,
  FIELD_TILES,
  HEDGE_RING_TILES,
  HOUSE_RING_TILES,
  ISLAND_FIELDS,
  ISLAND_TILES,
  LEVEL_Y,
  MARKET_FIELDS,
  MARKET_HALF_TILES,
  PLATEAU_LEVEL,
  PLAZA_RADIUS_TILES,
  TileKind,
  buildIsland,
  findPath,
  groundY,
  hash2,
  isWalkable,
  randomPlazaTile,
  smoothPath,
  tileIndex,
  tileToWorld,
  worldToTile,
  type Island,
} from "./islandLayout";

describe("islandLayout", () => {
  let island: Island;
  beforeAll(() => {
    island = buildIsland();
  });

  it("is a 10 × 10 field grid with a centred 4 × 4 market", () => {
    expect(ISLAND_FIELDS).toBe(10);
    expect(MARKET_FIELDS).toBe(4);
    expect(ISLAND_TILES).toBe(ISLAND_FIELDS * FIELD_TILES);
    // The market sits on fields 3..6 of 0..9 — symmetric around the centre.
    expect((ISLAND_FIELDS - MARKET_FIELDS) / 2).toBe(3);
    expect(CENTER_TILE - MARKET_HALF_TILES).toBe(3 * FIELD_TILES);
  });

  it("maps tiles to world metres and back around the island centre", () => {
    const [x, z] = tileToWorld(CENTER_TILE, CENTER_TILE);
    expect(x).toBeCloseTo(1); // half a tile east of the exact centre
    expect(z).toBeCloseTo(1);
    expect(worldToTile(x, z)).toEqual([CENTER_TILE, CENTER_TILE]);
    expect(worldToTile(-0.5, -0.5)).toEqual([CENTER_TILE - 1, CENTER_TILE - 1]);
  });

  it("is deterministic — the same island every build", () => {
    const again = buildIsland();
    expect(again.map.kind).toBe(island.map.kind); // cached
    // And stable in content: a fixed hash should not drift between platforms.
    expect(hash2(12, 34, 5)).toBe(hash2(12, 34, 5));
    expect(hash2(12, 34, 5)).not.toBe(hash2(34, 12, 5));
  });

  it("puts the sea around the land and a bay on the south coast", () => {
    const { map } = island;
    const corner = map.kind[tileIndex(map, 0, 0)];
    expect(corner).toBe(TileKind.water);
    // The bay: water south of the harbor, land north of it.
    expect(map.kind[tileIndex(map, CENTER_TILE, ISLAND_TILES - 2)]).toBe(TileKind.water);
    expect(map.kind[tileIndex(map, CENTER_TILE, CENTER_TILE + 40)]).not.toBe(TileKind.water);
    // Most of the grid is land (an island, not a rock in the sea).
    let land = 0;
    for (let i = 0; i < map.kind.length; i++) if (map.kind[i] !== TileKind.water) land++;
    expect(land / map.kind.length).toBeGreaterThan(0.45);
    expect(land / map.kind.length).toBeLessThan(0.85);
  });

  it("keeps the whole market district flat on the plateau level", () => {
    const { map } = island;
    for (let tz = CENTER_TILE - MARKET_HALF_TILES; tz < CENTER_TILE + MARKET_HALF_TILES; tz++) {
      for (let tx = CENTER_TILE - MARKET_HALF_TILES; tx < CENTER_TILE + MARKET_HALF_TILES; tx++) {
        const i = tileIndex(map, tx, tz);
        expect(map.kind[i], `tile ${tx},${tz}`).not.toBe(TileKind.water);
        expect(map.level[i], `tile ${tx},${tz}`).toBe(PLATEAU_LEVEL);
      }
    }
  });

  it("opens a paved square in the middle with a ring of houses around it", () => {
    const { map, content } = island;
    expect(map.kind[tileIndex(map, CENTER_TILE + 5, CENTER_TILE + 5)]).toBe(TileKind.plaza);
    expect(content.houses.length).toBe(10); // 12 slots, two taken by the Plugin Docks
    for (const h of content.houses) {
      const r = Math.hypot(h.x, h.z) / 2;
      expect(r).toBeGreaterThan(PLAZA_RADIUS_TILES);
      expect(r).toBeLessThan(HEDGE_RING_TILES);
      // Doors face the square: local +z rotated by `rotation` points at the centre.
      const fx = Math.sin(h.rotation);
      const fz = Math.cos(h.rotation);
      const toCentre = [-h.x, -h.z];
      const dot = fx * toCentre[0] + fz * toCentre[1];
      expect(dot).toBeGreaterThan(0);
    }
  });

  it("puts the Plugin Docks on the house ring, facing the square, with a reachable stand", () => {
    const { map, content } = island;
    const pose = content.kitPoses.plugins;
    expect(Math.hypot(pose.x, pose.z) / 2).toBeCloseTo(HOUSE_RING_TILES, 5);
    const fx = Math.sin(pose.rotation);
    const fz = Math.cos(pose.rotation);
    expect(fx * -pose.x + fz * -pose.z).toBeGreaterThan(0); // the front points at the centre
    const [sx, sz] = content.places.plugins.standTile;
    expect(isWalkable(map, sx, sz)).toBe(true);
    const [bx, bz] = worldToTile(pose.x, pose.z);
    expect(isWalkable(map, bx, bz)).toBe(false); // the footprint is blocked
  });

  it("leaves the four gates open in the hedge ring", () => {
    const { content } = island;
    const r = HEDGE_RING_TILES * 2;
    for (const [gx, gz] of [
      [0, -r],
      [0, r],
      [-r, 0],
      [r, 0],
    ]) {
      const nearest = Math.min(...content.hedges.map((h) => Math.hypot(h.x - gx, h.z - gz)));
      expect(nearest).toBeGreaterThan(4);
    }
    expect(content.hedges.length).toBeGreaterThan(100);
  });

  it("reads ground height from the level table and the dock from its own height", () => {
    const { map } = island;
    const [x, z] = tileToWorld(CENTER_TILE, CENTER_TILE);
    expect(groundY(map, x, z)).toBe(LEVEL_Y[PLATEAU_LEVEL]);
    const [dx, dz] = tileToWorld(CENTER_TILE, DOCK_TILES.to);
    expect(map.kind[tileIndex(map, CENTER_TILE, DOCK_TILES.to)]).toBe(TileKind.dock);
    expect(groundY(map, dx, dz)).toBeGreaterThan(LEVEL_Y[0]);
    expect(groundY(map, 10_000, 10_000)).toBe(LEVEL_Y[0]);
  });

  it("makes every place reachable on foot from the square", () => {
    const { map, content } = island;
    const start = randomPlazaTile(map, () => 0.5);
    expect(isWalkable(map, start[0], start[1])).toBe(true);
    for (const place of Object.values(content.places)) {
      expect(isWalkable(map, place.standTile[0], place.standTile[1]), place.id).toBe(true);
      const path = findPath(map, start, place.standTile);
      expect(path, `path to ${place.id}`).not.toBeNull();
      expect(path![0]).toEqual(start);
      expect(path![path!.length - 1]).toEqual(place.standTile);
      const smooth = smoothPath(map, path!);
      expect(smooth.length).toBeLessThanOrEqual(path!.length);
      expect(smooth[0]).toEqual(start);
      expect(smooth[smooth.length - 1]).toEqual(place.standTile);
    }
  });

  it("refuses paths into the sea and through buildings", () => {
    const { map, content } = island;
    const start = randomPlazaTile(map, () => 0.3);
    expect(findPath(map, start, [0, 0])).toBeNull();
    const house = content.houses[0];
    const [hx, hz] = worldToTile(house.x, house.z);
    expect(isWalkable(map, hx, hz)).toBe(false);
  });

  it("plants trees on grass only, never on roads or the square", () => {
    const { map, content } = island;
    expect(content.trees.length).toBeGreaterThan(150);
    for (const t of content.trees) {
      const [tx, tz] = worldToTile(t.x, t.z);
      const k = map.kind[tileIndex(map, tx, tz)];
      expect([TileKind.grass, TileKind.meadow]).toContain(k);
      expect(Math.hypot(tx + 0.5 - CENTER_TILE, tz + 0.5 - CENTER_TILE)).toBeGreaterThan(
        PLAZA_RADIUS_TILES,
      );
    }
  });
});
