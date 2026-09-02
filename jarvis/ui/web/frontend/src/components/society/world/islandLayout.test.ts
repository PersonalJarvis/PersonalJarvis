import { beforeAll, describe, expect, it } from "vitest";

import {
  CENTER_TILE,
  DOCK_TILES,
  FIELD_TILES,
  HEDGE_RING_TILES,
  HOUSE_RING_TILES,
  ISLAND_FIELDS,
  ISLAND_TILES,
  ISLETS,
  LEVEL_Y,
  MARKET_FIELDS,
  MARKET_HALF_TILES,
  PLATEAU_LEVEL,
  PLATEAU_RADIUS_TILES,
  PLAZA_RADIUS_TILES,
  PODIUM_LEVEL,
  REGIONS,
  RING_KIT_SLOTS,
  SNOW_LEVEL,
  SPOKES,
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

/** Tile under a normalised island coordinate. */
function tileAt(nx: number, nz: number): [number, number] {
  return [Math.floor(CENTER_TILE + nx * CENTER_TILE), Math.floor(CENTER_TILE + nz * CENTER_TILE)];
}

describe("islandLayout", () => {
  let island: Island;
  beforeAll(() => {
    island = buildIsland();
  });

  it("is a 16 × 16 field grid with a centred 4 × 4 market", () => {
    expect(ISLAND_FIELDS).toBe(16);
    expect(MARKET_FIELDS).toBe(4);
    expect(ISLAND_TILES).toBe(ISLAND_FIELDS * FIELD_TILES);
    // The market sits on fields 6..9 of 0..15 — symmetric around the centre.
    expect((ISLAND_FIELDS - MARKET_FIELDS) / 2).toBe(6);
    expect(CENTER_TILE - MARKET_HALF_TILES).toBe(6 * FIELD_TILES);
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
    expect(map.kind[tileIndex(map, ...tileAt(REGIONS.bay.x, REGIONS.bay.z))]).toBe(TileKind.water);
    expect(map.kind[tileIndex(map, CENTER_TILE, CENTER_TILE + 40)]).not.toBe(TileKind.water);
    // Roughly half the grid is land (an island with sea around it, not a rock, not a continent).
    let land = 0;
    for (let i = 0; i < map.kind.length; i++) if (map.kind[i] !== TileKind.water) land++;
    expect(land / map.kind.length).toBeGreaterThan(0.45);
    expect(land / map.kind.length).toBeLessThan(0.7);
  });

  it("carries every biome, from the beach to the snow cap", () => {
    const { map } = island;
    const counts = new Map<number, number>();
    let peak = 0;
    for (let i = 0; i < map.kind.length; i++) {
      counts.set(map.kind[i], (counts.get(map.kind[i]) ?? 0) + 1);
      if (map.level[i] > peak) peak = map.level[i];
    }
    for (const kind of Object.values(TileKind)) {
      expect(counts.get(kind) ?? 0, `kind ${kind}`).toBeGreaterThan(0);
    }
    expect(peak).toBe(LEVEL_Y.length - 1);
    // Snow only on the mountain top, never below the snow line.
    for (let i = 0; i < map.kind.length; i++) {
      if (map.kind[i] === TileKind.snow) expect(map.level[i]).toBeGreaterThanOrEqual(SNOW_LEVEL);
    }
    // The mountain is the north-western backdrop.
    const [mx, mz] = tileAt(REGIONS.mountain.x, REGIONS.mountain.z);
    expect(map.level[tileIndex(map, mx, mz)]).toBeGreaterThanOrEqual(SNOW_LEVEL);
  });

  it("encloses a lagoon in the cove and sets rock islets off the coast", () => {
    const { map } = island;
    const [lx, lz] = tileAt(REGIONS.lagoon.x, REGIONS.lagoon.z);
    expect(map.kind[tileIndex(map, lx, lz)]).toBe(TileKind.water);
    // Its sandbar: beach to the west and north of the water.
    const rim = Math.ceil(REGIONS.lagoon.r * CENTER_TILE) + 2;
    expect(map.kind[tileIndex(map, lx - rim, lz)]).toBe(TileKind.sand);
    expect(map.kind[tileIndex(map, lx, lz - rim)]).toBe(TileKind.sand);
    for (const islet of ISLETS) {
      const [ix, iz] = tileAt(islet.x, islet.z);
      expect(map.kind[tileIndex(map, ix, iz)], `islet ${islet.x},${islet.z}`).not.toBe(TileKind.water);
      const off = Math.ceil(islet.r * CENTER_TILE) + 3;
      expect(map.kind[tileIndex(map, ix + off, iz)]).toBe(TileKind.water);
    }
  });

  it("keeps the village plateau flat, with the hub one step up on its podium", () => {
    const { map, content } = island;
    const [hx, hz] = content.places.hub.tile;
    const r = PLATEAU_RADIUS_TILES - 4;
    for (let tz = CENTER_TILE - r; tz < CENTER_TILE + r; tz++) {
      for (let tx = CENTER_TILE - r; tx < CENTER_TILE + r; tx++) {
        if (Math.hypot(tx + 0.5 - CENTER_TILE, tz + 0.5 - CENTER_TILE) >= r) continue;
        const i = tileIndex(map, tx, tz);
        expect(map.kind[i], `tile ${tx},${tz}`).not.toBe(TileKind.water);
        // The podium rect; its front row is overpainted by the square's rim beds.
        const onPodium = Math.abs(tx - hx) <= 9 && Math.abs(tz - hz) <= 5;
        if (onPodium) expect([PLATEAU_LEVEL, PODIUM_LEVEL], `tile ${tx},${tz}`).toContain(map.level[i]);
        else expect(map.level[i], `tile ${tx},${tz}`).toBe(PLATEAU_LEVEL);
      }
    }
    expect(groundY(map, ...tileToWorld(hx, hz))).toBe(LEVEL_Y[PODIUM_LEVEL]);
  });

  it("opens a paved square in the middle with a ring of houses around it", () => {
    const { map, content } = island;
    expect(map.kind[tileIndex(map, CENTER_TILE + 5, CENTER_TILE + 5)]).toBe(TileKind.plaza);
    expect(content.houses.length).toBe(7); // 12 slots, five taken by the four ring hubs
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
      // No house stands on the hub's podium.
      const [tx, tz] = worldToTile(h.x, h.z);
      expect(map.level[tileIndex(map, tx, tz)]).toBe(PLATEAU_LEVEL);
    }
  });

  it("puts every ring hub on the house ring, facing the square, with a reachable stand", () => {
    const { map, content } = island;
    for (const [id, pose] of Object.entries(content.kitPoses)) {
      expect(Math.hypot(pose.x, pose.z) / 2, id).toBeCloseTo(HOUSE_RING_TILES, 5);
      const fx = Math.sin(pose.rotation);
      const fz = Math.cos(pose.rotation);
      expect(fx * -pose.x + fz * -pose.z, id).toBeGreaterThan(0); // the front points at the centre
      const [sx, sz] = content.places[id as keyof typeof content.places].standTile;
      expect(isWalkable(map, sx, sz), id).toBe(true);
      const [bx, bz] = worldToTile(pose.x, pose.z);
      expect(isWalkable(map, bx, bz), id).toBe(false); // the footprint is blocked
    }
    // No two hubs share a slot.
    const slots = Object.values(RING_KIT_SLOTS).flat();
    expect(new Set(slots).size).toBe(slots.length);
    // No ring hub stands on the hub's podium.
    for (const pose of Object.values(content.kitPoses)) {
      const [tx, tz] = worldToTile(pose.x, pose.z);
      expect(map.level[tileIndex(map, tx, tz)]).toBe(PLATEAU_LEVEL);
    }
  });

  it("leaves the four gates open in the hedge ring, the north one as wide as the hub", () => {
    const { content } = island;
    const r = HEDGE_RING_TILES * 2;
    // The podium (±18 m) sits astride the ring at the north: no hedge post on it.
    for (const h of content.hedges) {
      if (h.z < -r + 12 && Math.abs(h.x) < 18) throw new Error(`hedge on the podium at ${h.x},${h.z}`);
    }
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

  it("grades every spoke so it never climbs more than one step per tile", () => {
    const { map } = island;
    for (const spoke of SPOKES) {
      let prev = PLATEAU_LEVEL;
      for (let s = PLAZA_RADIUS_TILES - 1; s <= spoke.toTiles; s++) {
        const tx = CENTER_TILE + spoke.dir[0] * s;
        const tz = CENTER_TILE + spoke.dir[1] * s;
        const i = tileIndex(map, tx, tz);
        if (map.kind[i] === TileKind.water) continue;
        expect(Math.abs(map.level[i] - prev), `spoke ${spoke.dir} at ${s}`).toBeLessThanOrEqual(1);
        prev = map.level[i];
      }
    }
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

  it("plants each biome's trees on its own ground, never on roads or the square", () => {
    const { map, content } = island;
    expect(content.trees.length).toBeGreaterThan(800);
    const kinds = { round: 0, pine: 0, palm: 0 };
    for (const t of content.trees) {
      kinds[t.kind]++;
      const [tx, tz] = worldToTile(t.x, t.z);
      const k = map.kind[tileIndex(map, tx, tz)];
      expect([TileKind.grass, TileKind.meadow, TileKind.forest, TileKind.alpine, TileKind.sand]).toContain(k);
      if (k === TileKind.sand) expect(t.kind).toBe("palm");
      if (t.kind === "palm") expect(k).toBe(TileKind.sand);
      expect(Math.hypot(tx + 0.5 - CENTER_TILE, tz + 0.5 - CENTER_TILE)).toBeGreaterThan(
        PLAZA_RADIUS_TILES,
      );
    }
    expect(kinds.round).toBeGreaterThan(200);
    expect(kinds.pine).toBeGreaterThan(100);
    expect(kinds.palm).toBeGreaterThan(20);
  });

  it("scatters boulders over the high ground, never on pavement", () => {
    const { map, content } = island;
    expect(content.boulders.length).toBeGreaterThan(100);
    for (const b of content.boulders) {
      const [tx, tz] = worldToTile(b.x, b.z);
      const k = map.kind[tileIndex(map, tx, tz)];
      expect([TileKind.path, TileKind.plaza, TileKind.dock, TileKind.water]).not.toContain(k);
    }
  });
});
