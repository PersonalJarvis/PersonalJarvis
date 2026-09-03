import { beforeAll, describe, expect, it } from "vitest";

import {
  CENTER_TILE,
  DOCK_TILES,
  FIELD_TILES,
  FRONT_ROTATION,
  ISLAND_FIELDS,
  ISLAND_TILES,
  ISLETS,
  KIT_BLOCKS,
  KIT_FACING,
  LEVEL_Y,
  NORTH_ROAD,
  MARKET_FIELDS,
  MARKET_HALF_TILES,
  PLATEAU_LEVEL,
  PLATEAU_RADIUS_TILES,
  PLAZA_HALF_TILES,
  PODIUM_LEVEL,
  REGIONS,
  SUMMIT_LEVEL,
  SNOW_LEVEL,
  SPOKES,
  TOWN_EDGE_TILES,
  TileKind,
  applyBuildingYaws,
  buildIsland,
  defaultBuildingYaw,
  blockEdges,
  cornerRotation,
  findPath,
  groundY,
  hash2,
  houseId,
  isPavedZone,
  isWalkable,
  kitId,
  nearestWalkable,
  randomPlazaTile,
  smoothPath,
  tileIndex,
  tileToWorld,
  squareFront,
  townBlocks,
  townZone,
  worldToTile,
  type Island,
  type KitPlace,
  type KitPose,
} from "./islandLayout";

/** Tile under a normalised island coordinate. */
function tileAt(nx: number, nz: number): [number, number] {
  return [Math.floor(CENTER_TILE + nx * CENTER_TILE), Math.floor(CENTER_TILE + nz * CENTER_TILE)];
}

/** Angles rounded to four places, so the eight grid headings compare exactly. */
function round4(a: number): number {
  return Math.round(a * 1e4) / 1e4;
}

/**
 * The eight headings the town is designed in: the four compass fronts and the
 * four diagonals a corner building takes.
 */
const HEADING_GRID = [
  ...Object.values(FRONT_ROTATION),
  ...([
    [1, 1],
    [1, -1],
    [-1, 1],
    [-1, -1],
  ] as const).map(([sx, sz]) => cornerRotation(sx, sz)),
].map(round4);

/** Whether a heading is one of the four diagonals. */
function isDiagonal(rotation: number): boolean {
  return !Object.values(FRONT_ROTATION).some((r) => round4(r) === round4(rotation));
}

/** Whether walking out of a door at (x, z) heading `rotation` gets closer to the square. */
function looksAtSquare(x: number, z: number, rotation: number): boolean {
  const ahead = 6;
  const before = Math.hypot(x, z);
  const after = Math.hypot(x + Math.sin(rotation) * ahead, z + Math.cos(rotation) * ahead);
  return after < before - 1e-9;
}

/** The first paved tile within `limit` metres straight out of a door, or null. */
function pavingAhead(x: number, z: number, rotation: number, limit: number): [number, number] | null {
  for (let ahead = 1; ahead <= limit; ahead += 0.5) {
    const t = worldToTile(x + Math.sin(rotation) * ahead, z + Math.cos(rotation) * ahead);
    if (isPavedZone(townZone(t[0] - CENTER_TILE, t[1] - CENTER_TILE))) return t;
  }
  return null;
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

  it("keeps the town plateau flat, with the hub one step up on its podium beyond it", () => {
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

  it("lays the town out in blocks: every house fronts a street and looks at the square", () => {
    const { map, content } = island;
    expect(map.kind[tileIndex(map, CENTER_TILE + 5, CENTER_TILE + 5)]).toBe(TileKind.plaza);
    expect(map.kind[tileIndex(map, CENTER_TILE + PLAZA_HALF_TILES + 2, CENTER_TILE)]).toBe(TileKind.path);
    // Twenty blocks. The ten inner ones carry the halls — the public half of
    // the town, one hall per kind of work an agent can be seen doing; the outer
    // band and what is left of the inner one carry the houses.
    const blocks = townBlocks();
    expect(blocks.length).toBe(20);
    expect(
      blocks
        .filter((b) => b.kit)
        .map((b) => b.kit)
        .sort(),
    ).toEqual([
      "civic",
      "cli",
      "comms",
      "desktop",
      "gallery",
      "mcp",
      "models",
      "plugins",
      "skills",
      "web",
    ]);
    // Every block without a hall still carries houses, and nothing is empty.
    expect(content.houses.length).toBeGreaterThan(0);
    expect(new Set(content.houses.map((h) => h.slot)).size).toBe(content.houses.length);
    for (const h of content.houses) {
      // A door looks at the market square: walking out of it gets you closer.
      expect(looksAtSquare(h.x, h.z, h.rotation), `house ${h.slot}`).toBe(true);
      // It rests on the town's grid — an axis heading, or 45° on a corner block.
      expect(HEADING_GRID, `house ${h.slot}`).toContain(round4(h.rotation));
      expect(h.rotation).toBe(h.defaultRotation);
      expect(defaultBuildingYaw(houseId(h.slot))).toBe(h.defaultRotation);
      // It stands on a block of the plateau …
      const [tx, tz] = worldToTile(h.x, h.z);
      expect(townZone(tx - CENTER_TILE, tz - CENTER_TILE), `house ${h.slot}`).toBe("block");
      expect(map.level[tileIndex(map, tx, tz)]).toBe(PLATEAU_LEVEL);
      // … and the walk out of its door reaches paving — the street or alley it
      // fronts, never the square itself. A house on the diagonal of a corner
      // block crosses its own front garden first, so it is given more room.
      const paved = pavingAhead(h.x, h.z, h.rotation, isDiagonal(h.rotation) ? 14 : h.d + 2);
      expect(paved, `house ${h.slot} finds no paving past its door`).not.toBeNull();
      const [fx, fz] = paved as [number, number];
      const zone = townZone(fx - CENTER_TILE, fz - CENTER_TILE);
      expect(isPavedZone(zone) && zone !== "square", `house ${h.slot} fronts ${zone}`).toBe(true);
      expect(isWalkable(map, fx, fz), `house ${h.slot}`).toBe(true);
    }
    // Houses stand in every quadrant, so it reads as one town around the square.
    const quadrants = new Set(content.houses.map((h) => `${Math.sign(h.x)},${Math.sign(h.z)}`));
    expect(quadrants.size).toBe(4);
    // No two houses overlap.
    for (const a of content.houses) {
      for (const b of content.houses) {
        if (a.slot >= b.slot) continue;
        expect(Math.max(Math.abs(a.x - b.x), Math.abs(a.z - b.z)), `houses ${a.slot}/${b.slot}`).toBeGreaterThanOrEqual(5);
      }
    }
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

  it("stands every hall on its own block with its door on the street, the foundry on the summit", () => {
    const { map, content } = island;
    for (const [id, pose] of Object.entries(content.kitPoses) as Array<[KitPlace, KitPose]>) {
      const [sx, sz] = content.places[id].standTile;
      expect(isWalkable(map, sx, sz), id).toBe(true);
      const [bx, bz] = worldToTile(pose.x, pose.z);
      expect(isWalkable(map, bx, bz), id).toBe(false); // the footprint is blocked
      const level = map.level[tileIndex(map, bx, bz)];
      if (id === "foundry") {
        expect(level).toBe(SUMMIT_LEVEL);
        expect(pose.rotation).toBe(KIT_FACING.foundry);
        continue;
      }
      expect(level, id).toBe(PLATEAU_LEVEL);
      expect(townZone(bx - CENTER_TILE, bz - CENTER_TILE), id).toBe("block");
      const lot = KIT_BLOCKS[id];
      const block = townBlocks().find((b) => b.kit === id);
      if (block?.corner) {
        // A corner block has no side that looks at the square, so its hall
        // stands on the diagonal and looks down it (maintainer, 2026-09-03).
        expect(pose.rotation, id).toBe(cornerRotation(lot.sx, lot.sz));
      } else {
        expect(pose.rotation, id).toBe(FRONT_ROTATION[squareFront(lot.edge, lot.sx, lot.sz)]);
      }
      expect(looksAtSquare(pose.x, pose.z, pose.rotation), id).toBe(true);
      // The visitor stands on the paving in front of the door: the frame
      // street, or the alley that continues it past the corner block.
      expect(["street", "alley"], id).toContain(townZone(sx - CENTER_TILE, sz - CENTER_TILE));
    }
    // No two halls share a block.
    const lots = Object.values(KIT_BLOCKS).map((l) => `${l.template}:${l.sx}:${l.sz}`);
    expect(new Set(lots).size).toBe(lots.length);
  });

  it("hedges the two sides of every block turned away from the square", () => {
    const { map, content } = island;
    const hedged = new Set(content.hedges.map((h) => worldToTile(h.x, h.z).join(",")));
    const at = (kx: number, kz: number) => hedged.has(`${CENTER_TILE + kx},${CENTER_TILE + kz}`);
    for (const b of townBlocks()) {
      const { x0, x1, z0, z1 } = b.rect;
      const { nearX, farX, nearZ, farZ } = blockEdges(b);
      for (let kx = x0; kx <= x1; kx++) expect(at(kx, farZ), `far row ${kx},${farZ}`).toBe(true);
      for (let kz = z0; kz <= z1; kz++) expect(at(farX, kz), `far column ${farX},${kz}`).toBe(true);
      // The two near sides belong to the doors that look at the square.
      for (let kx = x0; kx <= x1; kx++) {
        if (kx !== farX) expect(at(kx, nearZ), `near row ${kx},${nearZ}`).toBe(false);
      }
      for (let kz = z0; kz <= z1; kz++) {
        if (kz !== farZ) expect(at(nearX, kz), `near column ${nearX},${kz}`).toBe(false);
      }
    }
    expect(content.hedges.length).toBeGreaterThan(200);
    for (const h of content.hedges) expect(isWalkable(map, ...worldToTile(h.x, h.z))).toBe(false);
    // The streets between the blocks stay open: a walk right round the square's frame.
    const loop = findPath(map, [CENTER_TILE + 14, CENTER_TILE - 14], [CENTER_TILE - 14, CENTER_TILE + 14]);
    expect(loop).not.toBeNull();
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
      for (let s = TOWN_EDGE_TILES - 1; s <= spoke.toTiles; s++) {
        const tx = CENTER_TILE + spoke.dir[0] * s;
        const tz = CENTER_TILE + spoke.dir[1] * s;
        const i = tileIndex(map, tx, tz);
        if (map.kind[i] === TileKind.water) continue;
        expect(Math.abs(map.level[i] - prev), `spoke ${spoke.dir} at ${s}`).toBeLessThanOrEqual(1);
        prev = map.level[i];
      }
    }
    // The archive road: paved along all three legs, graded the same way, and
    // never on the hub's podium — the hub closes the avenue, the road goes round.
    const [hx, hz] = island.content.places.hub.tile;
    let prev = PLATEAU_LEVEL;
    for (const leg of NORTH_ROAD) {
      for (let s = 0; s <= leg.length; s++) {
        const tx = leg.from[0] + leg.dir[0] * s;
        const tz = leg.from[1] + leg.dir[1] * s;
        const i = tileIndex(map, tx, tz);
        expect(map.kind[i], `archive road at ${tx},${tz}`).toBe(TileKind.path);
        expect(Math.abs(map.level[i] - prev), `archive road at ${tx},${tz}`).toBeLessThanOrEqual(1);
        expect(Math.abs(tx - hx) > 10 || Math.abs(tz - hz) > 5, `archive road on the podium at ${tx},${tz}`).toBe(true);
        prev = map.level[i];
      }
    }
    // Its last leg arrives at the archive's stand.
    const [ax, az] = island.content.places.archive.standTile;
    const last = NORTH_ROAD[NORTH_ROAD.length - 1];
    expect(last.from[0]).toBe(ax);
    expect(last.from[1] + last.dir[1] * last.length).toBeLessThanOrEqual(az);
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
      expect(isPavedZone(townZone(tx - CENTER_TILE, tz - CENTER_TILE)), `tree on paving at ${tx},${tz}`).toBe(false);
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
