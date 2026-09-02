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
  KIT_FACING,
  LEVEL_Y,
  MARKET_FIELDS,
  MARKET_HALF_TILES,
  PLATEAU_LEVEL,
  PLATEAU_RADIUS_TILES,
  PLAZA_RADIUS_TILES,
  PODIUM_LEVEL,
  REGIONS,
  RING_KIT_SLOTS,
  SUMMIT_LEVEL,
  SNOW_LEVEL,
  SPOKES,
  TileKind,
  applyBuildingYaws,
  buildIsland,
  defaultBuildingYaw,
  facesCamera,
  findPath,
  groundY,
  hash2,
  houseDefaultRotation,
  houseId,
  isWalkable,
  kitId,
  nearestWalkable,
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
    expect(content.houses.length).toBe(7); // 12 slots, five taken by the ring hubs
    for (const h of content.houses) {
      const r = Math.hypot(h.x, h.z) / 2;
      expect(r).toBeGreaterThan(PLAZA_RADIUS_TILES);
      expect(r).toBeLessThan(HEDGE_RING_TILES);
      // A door faces the square or the ring road: local +z rotated by
      // `rotation` points along the radius, one way or the other …
      const fx = Math.sin(h.rotation);
      const fz = Math.cos(h.rotation);
      const along = (fx * -h.x + fz * -h.z) / Math.hypot(h.x, h.z);
      expect(Math.abs(along), `house ${h.slot}`).toBeCloseTo(1, 6);
      // … and never away from the camera: no house shows the viewer its back.
      expect(facesCamera(h.rotation), `house ${h.slot}`).toBe(true);
      expect(h.rotation).toBe(h.defaultRotation);
      expect(h.defaultRotation).toBeCloseTo(houseDefaultRotation(h.x, h.z), 9);
      // No house stands on the hub's podium.
      const [tx, tz] = worldToTile(h.x, h.z);
      expect(map.level[tileIndex(map, tx, tz)]).toBe(PLATEAU_LEVEL);
    }
    // The houses stand in the south-east half, the camera's side of the ring,
    // so most of them face the road — that is the point of the rule.
    const towardRoad = content.houses.filter((h) => Math.sin(h.rotation) * h.x + Math.cos(h.rotation) * h.z > 0);
    expect(towardRoad.length).toBeGreaterThanOrEqual(4);
    // A house on the far side would keep its door on the square.
    expect(facesCamera(houseDefaultRotation(-30, -30))).toBe(true);
    expect(houseDefaultRotation(-30, -30)).toBeCloseTo(Math.atan2(30, 30), 9);
  });

  it("blocks the square's furniture and the landmarks' add-ons so nobody walks through a bench", () => {
    const { map, content } = island;
    // The Quest Board's plinth and the ring bench: nothing walkable inside 6 m.
    for (const [x, z] of [
      [1, 1],
      [5, 3],
      [-5, 1],
      [1, -5],
    ]) {
      expect(isWalkable(map, ...worldToTile(x, z)), `square ${x},${z}`).toBe(false);
    }
    expect(isWalkable(map, ...worldToTile(7, 1))).toBe(true);
    // The long table with its benches, south of the monument.
    expect(isWalkable(map, ...worldToTile(0, 8.5))).toBe(false);
    expect(isWalkable(map, ...worldToTile(-4, 7))).toBe(false);
    expect(isWalkable(map, ...worldToTile(0, 12))).toBe(true);
    // The meeting stand tile beside the table stays reachable.
    const meet = content.places.market.standTile;
    expect(isWalkable(map, meet[0], meet[1])).toBe(true);
    // A walk from the west of the square to the east goes AROUND the middle.
    const path = findPath(map, worldToTile(-16, 1), worldToTile(16, 1));
    expect(path).not.toBeNull();
    for (const [tx, tz] of path!) {
      const [x, z] = tileToWorld(tx, tz);
      expect(Math.hypot(x, z)).toBeGreaterThan(5);
    }
    // The hub's wings and reflecting pool, the harbor kiosk, the keeper's hut.
    const [hx, hz] = tileToWorld(...content.places.hub.tile);
    expect(isWalkable(map, ...worldToTile(hx + 15.5, hz + 1))).toBe(false);
    expect(isWalkable(map, ...worldToTile(hx, hz + 13.2))).toBe(false);
    const [bx, bz] = tileToWorld(...content.places.harbor.tile);
    expect(isWalkable(map, ...worldToTile(bx - 7, bz + 2))).toBe(false);
    const [lx, lz] = tileToWorld(...content.places.lighthouse.tile);
    expect(isWalkable(map, ...worldToTile(lx - 6.5, lz + 3.9))).toBe(false);
    // Every lamp post and hedge segment stands on a blocked tile — except a
    // lamp on a dock plank (the planks stay open) or on a place's stand tile
    // (a stand tile is always kept reachable; the figure stands at the lamp).
    const stands = new Set(Object.values(content.places).map((p) => p.standTile.join(",")));
    for (const l of content.lamps) {
      const [tx, tz] = worldToTile(l.x, l.z);
      if (map.kind[tileIndex(map, tx, tz)] === TileKind.dock || stands.has(`${tx},${tz}`)) continue;
      expect(isWalkable(map, tx, tz), `lamp ${l.x},${l.z}`).toBe(false);
    }
    for (const h of content.hedges) expect(isWalkable(map, ...worldToTile(h.x, h.z))).toBe(false);
    // Idle wandering never picks a tile inside the bench ring.
    for (let i = 0; i < 40; i++) {
      const [tx, tz] = randomPlazaTile(map, () => (i * 0.137) % 1);
      const [x, z] = tileToWorld(tx, tz);
      expect(Math.hypot(x, z)).toBeGreaterThan(6);
    }
  });

  it("turns buildings in place and re-stamps their footprints", () => {
    const { map, content } = island;
    const house = content.houses[0];
    const rest = house.defaultRotation;
    // With no overrides the blocked layer equals the built one.
    const before = map.blocked.slice();
    applyBuildingYaws(island, {});
    expect(map.blocked).toEqual(before);
    // Turning a house a quarter round moves its blocked tiles.
    applyBuildingYaws(island, { [houseId(house.slot)]: rest + Math.PI / 2 });
    expect(house.rotation).toBeCloseTo(rest + Math.PI / 2, 9);
    expect(map.blocked).not.toEqual(before);
    // Every stand tile is still walkable, and every house tile still blocked.
    for (const p of Object.values(content.places)) expect(isWalkable(map, p.standTile[0], p.standTile[1]), p.id).toBe(true);
    expect(isWalkable(map, ...worldToTile(house.x, house.z))).toBe(false);
    // Back to rest restores the exact layer.
    applyBuildingYaws(island, {});
    expect(house.rotation).toBe(rest);
    expect(map.blocked).toEqual(before);
    // Every turnable building has a designed heading to return to.
    expect(defaultBuildingYaw(houseId(house.slot))).toBe(rest);
    expect(defaultBuildingYaw(kitId("cli"))).toBeCloseTo(content.kitPoses.cli.rotation, 9);
    // A figure caught inside a footprint finds the nearest free tile.
    const inside = worldToTile(house.x, house.z);
    const out = nearestWalkable(map, inside[0], inside[1]);
    expect(out).not.toBeNull();
    expect(isWalkable(map, out![0], out![1])).toBe(true);
    expect(Math.max(Math.abs(out![0] - inside[0]), Math.abs(out![1] - inside[1]))).toBeLessThanOrEqual(3);
  });

  it("puts the Plugin Docks on the house ring, facing the square, with a reachable stand", () => {
    const { map, content } = island;
    for (const [id, pose] of Object.entries(content.kitPoses)) {
      // Ring hubs stand on the house ring; a hub with an anchor of its own does
      // not — the Agent Foundry crowns the mountain (SUMMIT_TILE).
      if (id in RING_KIT_SLOTS) {
        expect(Math.hypot(pose.x, pose.z) / 2, id).toBeCloseTo(HOUSE_RING_TILES, 5);
      }
      const fx = Math.sin(pose.rotation);
      const fz = Math.cos(pose.rotation);
      // The front points at the centre, unless the building faces the road on purpose.
      if (!(id in KIT_FACING)) expect(fx * -pose.x + fz * -pose.z, id).toBeGreaterThan(0);
      const [sx, sz] = content.places[id as keyof typeof content.places].standTile;
      expect(isWalkable(map, sx, sz), id).toBe(true);
      const [bx, bz] = worldToTile(pose.x, pose.z);
      expect(isWalkable(map, bx, bz), id).toBe(false); // the footprint is blocked
    }
    // No two hubs share a slot.
    const slots = Object.values(RING_KIT_SLOTS).flat();
    expect(new Set(slots).size).toBe(slots.length);
    // No ring hub stands on the hub's podium; the summit hub stands on its own
    // terrace on the mountain, far above the village.
    for (const [id, pose] of Object.entries(content.kitPoses)) {
      const [tx, tz] = worldToTile(pose.x, pose.z);
      const level = map.level[tileIndex(map, tx, tz)];
      expect(level, id).toBe(id in RING_KIT_SLOTS ? PLATEAU_LEVEL : SUMMIT_LEVEL);
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
      expect([TileKind.grass, TileKind.meadow, TileKind.forest, TileKind.alpine, TileKind.sand, TileKind.heath, TileKind.dry]).toContain(k);
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

  it("cuts the mine into a cliff at the end of its own road, and lights the village", () => {
    const { map, content } = island;
    const [mx, mz] = content.places.mine.tile;
    // The forecourt is quarry floor; the cliff behind it stands three steps higher, as rock.
    expect(map.kind[tileIndex(map, mx, mz)]).toBe(TileKind.quarry);
    expect(map.kind[tileIndex(map, mx, mz - 8)]).toBe(TileKind.rock);
    expect(map.level[tileIndex(map, mx, mz - 8)] - map.level[tileIndex(map, mx, mz)]).toBeGreaterThanOrEqual(3);
    // The branch road reaches the forecourt from the north spoke.
    expect(map.kind[tileIndex(map, mx + 12, mz)]).toBe(TileKind.path);
    expect(content.festoonPoles.length).toBe(8);
    expect(content.lamps.length).toBeGreaterThan(50);
    expect(content.reeds.length).toBeGreaterThan(40);
    // Marsh pools are still water, their own kind: the sea's surf never reaches them.
    let pools = 0;
    for (let i = 0; i < map.kind.length; i++) if (map.kind[i] === TileKind.pool) pools++;
    expect(pools).toBeGreaterThan(10);
    const [cx, cz] = worldToTile(content.campfire.x, content.campfire.z);
    expect(map.kind[tileIndex(map, cx, cz)]).toBe(TileKind.sand);
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
