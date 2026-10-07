import { describe, expect, it } from "vitest";
import { ARCADE_GAMES, cabinetId, gameForCabinet } from "./arcadeGames";
import { arcadeDecor, buildArcadeLayout, cabinetPlaySpot, GAME_ROOMS, hallLanes, ROOM_ACCENT, SNACK_TABLE_HALF, uniqueWalls } from "./arcadeFloorLayout";
import { FURNITURE_SIZE, footprint, roomAt, wallRect, wallsOf, type Rect } from "../office/officeLayout";
import { buildNavGrid, findPath, isWalkable } from "../office/officeNav";
import { arrivalPose } from "../office/officeFloors";

const overlaps = (a: Rect, b: Rect) => a.minX < b.maxX - 1e-9 && b.minX < a.maxX - 1e-9 && a.minZ < b.maxZ - 1e-9 && b.minZ < a.maxZ - 1e-9;
const inside = (r: Rect, outer: Rect) => r.minX >= outer.minX && r.maxX <= outer.maxX && r.minZ >= outer.minZ && r.maxZ <= outer.maxZ;

describe("arcade floor layout", () => {
  const layout = buildArcadeLayout();
  const grid = buildNavGrid(layout);
  const cabinets = layout.furniture.filter((f) => f.kind === "retroCabinet");
  const pose = arrivalPose(layout, "elevator");
  const room = (kind: string) => layout.rooms.find((r) => r.kind === kind)!;

  it("is the arcade variant with no agents' places on it", () => {
    expect(layout.variant).toBe("arcade");
    expect(layout.departments).toEqual([]);
    expect(layout.lead.desks).toEqual([]);
    expect(layout.spots).toEqual([]);
    expect(layout.command).toBeUndefined();
    expect(layout.checkpoints.map((c) => c.id)).toEqual(["elevator"]);
  });

  it("is deterministic", () => {
    expect(buildArcadeLayout()).toEqual(layout);
  });

  it("floats on a slab the size of the floors below", () => {
    const { minX, maxX, minZ, maxZ } = layout.bounds;
    expect(maxX - minX).toBeGreaterThanOrEqual(24);
    expect(maxX - minX).toBeLessThanOrEqual(30);
    expect(maxZ - minZ).toBeGreaterThanOrEqual(18);
    expect(maxZ - minZ).toBeLessThanOrEqual(22);
    expect(inside(layout.floor, layout.bounds)).toBe(true);
  });

  it("has three game rooms in the north, the open hall between, foyer, prize shop and snack bar in the south", () => {
    expect(layout.rooms.map((r) => r.kind)).toEqual(["classics", "puzzle", "action", "arcade", "foyer", "prizes", "snack"]);
    for (const a of layout.rooms) {
      expect(inside(a, layout.bounds)).toBe(true);
      expect(ROOM_ACCENT[a.kind], a.kind).toBeDefined();
      for (const b of layout.rooms) if (a !== b) expect(overlaps(a, b), `${a.kind} × ${b.kind}`).toBe(false);
    }
    for (const kind of ["classics", "puzzle", "action"]) expect(room(kind).maxZ).toBeLessThanOrEqual(room("arcade").minZ);
    for (const kind of ["foyer", "prizes", "snack"]) expect(room(kind).minZ).toBeGreaterThanOrEqual(room("arcade").maxZ);
    expect(room("arcade").walled).toBe(false);
    for (const r of layout.rooms.filter((x) => x.kind !== "arcade")) {
      expect(r.walled, r.kind).toBe(true);
      expect(r.doors, r.kind).toHaveLength(1);
    }
  });

  it("opens every walled room onto the hall, the doors facing each other on three lanes", () => {
    const hall = room("arcade");
    for (const r of layout.rooms.filter((x) => x.walled)) {
      const door = r.doors[0];
      expect(door.side, r.kind).toBe(r.maxZ <= hall.minZ ? "south" : "north");
      expect(door.at).toBeGreaterThan(r.minX + door.width / 2);
      expect(door.at).toBeLessThan(r.maxX - door.width / 2);
    }
    const lanes = hallLanes(layout);
    expect(lanes).toHaveLength(3);
    const southDoors = ["foyer", "prizes", "snack"].map((k) => room(k).doors[0].at);
    expect(southDoors).toEqual(lanes);
    // Nothing solid in the hall stands on a lane.
    for (const item of layout.furniture.filter((f) => f.room === "arcade")) {
      const r = footprint(item);
      for (const x of lanes) expect(r.maxX < x - 0.9 || r.minX > x + 0.9, `${item.id} on lane ${x}`).toBe(true);
    }
  });

  it("draws and blocks every wall run once", () => {
    const all = layout.rooms.flatMap(wallsOf);
    expect(layout.walls.length).toBeLessThan(all.length);
    expect(uniqueWalls(layout.rooms)).toEqual(layout.walls);
    const keys = layout.walls.map((w) => [w.x1, w.z1, w.x2, w.z2].map((v) => v.toFixed(3)).join(","));
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("groups the games by room and puts every one in exactly one cabinet", () => {
    expect(Object.values(GAME_ROOMS).flat().sort()).toEqual(ARCADE_GAMES.map((g) => g.id).sort());
    expect(cabinets).toHaveLength(ARCADE_GAMES.length);
    for (const game of ARCADE_GAMES) expect(cabinets.filter((c) => c.id === cabinetId(game.id))).toHaveLength(1);
    for (const [kind, games] of Object.entries(GAME_ROOMS)) {
      const inRoom = cabinets.filter((c) => c.room === kind).sort((a, b) => a.x - b.x);
      expect(inRoom.map((c) => gameForCabinet(c.id)?.id)).toEqual(games);
    }
    expect(new Set(layout.furniture.map((f) => f.id)).size).toBe(layout.furniture.length);
  });

  it("stands every cabinet on its room's back wall, screen to the south", () => {
    for (const cabinet of cabinets) {
      expect(cabinet.rotationY).toBe(0);
      const r = room(cabinet.room);
      expect(footprint(cabinet).minZ - r.minZ, cabinet.id).toBeLessThan(0.15);
    }
  });

  it("puts every furniture piece in the room it names", () => {
    for (const item of layout.furniture) expect(roomAt(layout, item)?.kind, item.id).toBe(item.room);
  });

  it("keeps solid furniture, tables and walls apart and inside the slab", () => {
    const solids = layout.furniture.filter((f) => FURNITURE_SIZE[f.kind].solid).map((f) => ({ id: f.id, rect: footprint(f) }));
    const tables = arcadeDecor(layout.rooms).tables.map((t, i) => ({
      id: `table-${i}`, rect: { minX: t.x - SNACK_TABLE_HALF, maxX: t.x + SNACK_TABLE_HALF, minZ: t.z - SNACK_TABLE_HALF, maxZ: t.z + SNACK_TABLE_HALF },
    }));
    const walls = layout.walls.map((w, i) => ({ id: `wall-${i}`, rect: wallRect(w) }));
    const things = [...solids, ...tables];
    expect(tables).toHaveLength(4);
    for (let i = 0; i < things.length; i += 1) {
      expect(inside(things[i].rect, layout.bounds), things[i].id).toBe(true);
      for (let j = i + 1; j < things.length; j += 1) {
        expect(overlaps(things[i].rect, things[j].rect), `${things[i].id} × ${things[j].id}`).toBe(false);
      }
      for (const wall of walls) expect(overlaps(things[i].rect, wall.rect), `${things[i].id} × ${wall.id}`).toBe(false);
    }
    for (const { rect } of [...things, ...walls]) expect(layout.obstacles).toContainEqual(rect);
  });

  it("keeps the floor logos and the dance floor clear of everything solid", () => {
    const decor = arcadeDecor(layout.rooms);
    const solids = layout.obstacles;
    const flats: Rect[] = [decor.pad, ...decor.logos.map((l) => ({ minX: l.x - l.r, maxX: l.x + l.r, minZ: l.z - l.r, maxZ: l.z + l.r }))];
    expect(decor.logos.map((l) => l.room).sort()).toEqual(["action", "arcade", "classics", "foyer", "puzzle"]);
    for (const flat of flats) for (const solid of solids) expect(overlaps(flat, solid)).toBe(false);
  });

  it("can reach every cabinet's play spot from the elevator", () => {
    expect(isWalkable(grid, pose)).toBe(true);
    expect(isWalkable(grid, layout.spawn)).toBe(true);
    expect(isWalkable(grid, layout.arrival)).toBe(true);
    for (const cabinet of cabinets) {
      const spot = cabinetPlaySpot(cabinet);
      expect(isWalkable(grid, spot), cabinet.id).toBe(true);
      const path = findPath(grid, pose, spot);
      expect(path, cabinet.id).not.toBeNull();
      expect(path?.at(-1)).toEqual(spot);
    }
  });

  it("keeps the play spots of different cabinets at least a metre apart", () => {
    const spots = cabinets.map(cabinetPlaySpot);
    for (let i = 0; i < spots.length; i += 1) {
      for (let j = i + 1; j < spots.length; j += 1) {
        expect(Math.hypot(spots[i].x - spots[j].x, spots[i].z - spots[j].z)).toBeGreaterThanOrEqual(1.0);
      }
    }
  });

  it("arrives in the foyer, clear of the token machines", () => {
    const stop = layout.checkpoints[0];
    expect(stop.room).toBe("foyer");
    expect(roomAt(layout, layout.arrival)?.kind).toBe("foyer");
    for (const token of layout.furniture.filter((f) => f.kind === "tokenMachine")) {
      expect(Math.hypot(token.x - stop.x, token.z - stop.z)).toBeGreaterThan(stop.radius + 0.4);
    }
  });

  it("reaches the counters, claw machines, token machines, pinballs and the air hockey on foot", () => {
    const front = (id: string) => {
      const item = layout.furniture.find((f) => f.id === id)!;
      const reach = FURNITURE_SIZE[item.kind].d / 2 + 0.5;
      return { x: item.x + Math.sin(item.rotationY) * reach, z: item.z + Math.cos(item.rotationY) * reach };
    };
    const ids = layout.furniture.filter((f) => ["prizeCounter", "snackCounter", "clawMachine", "tokenMachine", "pinball", "airHockey"].includes(f.kind)).map((f) => f.id);
    expect(ids.length).toBeGreaterThanOrEqual(12);
    for (const id of ids) {
      expect(isWalkable(grid, front(id)), id).toBe(true);
      expect(findPath(grid, pose, front(id)), id).not.toBeNull();
    }
  });

  it("lets the person walk from the foyer through the hall into every room", () => {
    for (const r of layout.rooms) {
      const centre = { x: (r.minX + r.maxX) / 2, z: r.kind === "arcade" ? (r.minZ + r.maxZ) / 2 + 1.2 : (r.minZ + r.maxZ) / 2 };
      const target = isWalkable(grid, centre) ? centre : { x: r.doors[0]?.at ?? centre.x, z: r.maxZ <= room("arcade").minZ ? r.maxZ - 1 : r.minZ + 1 };
      expect(isWalkable(grid, target), r.kind).toBe(true);
      expect(findPath(grid, pose, target), r.kind).not.toBeNull();
    }
  });
});
