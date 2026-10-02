import { describe, expect, it } from "vitest";
import { ARCADE_GAMES, cabinetId, gameForCabinet } from "./arcadeGames";
import { arcadeDecor, buildArcadeLayout, cabinetPlaySpot, SNACK_TABLE_HALF } from "./arcadeFloorLayout";
import { archPosts, ARCH, FURNITURE_SIZE, footprint, roomAt, wallRect, type Rect } from "../office/officeLayout";
import { buildNavGrid, findPath, isWalkable } from "../office/officeNav";
import { arrivalPose } from "../office/officeFloors";

const overlaps = (a: Rect, b: Rect) => a.minX < b.maxX - 1e-9 && b.minX < a.maxX - 1e-9 && a.minZ < b.maxZ - 1e-9 && b.minZ < a.maxZ - 1e-9;
const inside = (r: Rect, outer: Rect) => r.minX >= outer.minX && r.maxX <= outer.maxX && r.minZ >= outer.minZ && r.maxZ <= outer.maxZ;

describe("arcade floor layout", () => {
  const layout = buildArcadeLayout();
  const grid = buildNavGrid(layout);
  const cabinets = layout.furniture.filter((f) => f.kind === "retroCabinet");

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

  it("has a compact slab of the size the other floors float at", () => {
    const { minX, maxX, minZ, maxZ } = layout.bounds;
    expect(maxX - minX).toBeGreaterThanOrEqual(22);
    expect(maxX - minX).toBeLessThanOrEqual(28);
    expect(maxZ - minZ).toBeGreaterThanOrEqual(16);
    expect(maxZ - minZ).toBeLessThanOrEqual(20);
    expect(inside(layout.floor, layout.bounds)).toBe(true);
  });

  it("has the hall, the prize corner and the snack bar, side by side", () => {
    expect(layout.rooms.map((r) => r.kind).sort()).toEqual(["arcade", "prizes", "snack"]);
    for (const a of layout.rooms) {
      expect(inside(a, layout.bounds)).toBe(true);
      for (const b of layout.rooms) if (a !== b) expect(overlaps(a, b)).toBe(false);
    }
    const kinds = (room: string) => layout.furniture.filter((f) => f.room === room).map((f) => f.kind);
    expect(kinds("prizes")).toEqual(expect.arrayContaining(["prizeCounter", "tokenMachine", "clawMachine"]));
    expect(kinds("snack")).toEqual(expect.arrayContaining(["snackCounter", "elevator"]));
    expect(kinds("arcade")).toEqual(expect.arrayContaining(["retroCabinet", "pinball", "airHockey"]));
  });

  it("puts every furniture piece in the room it names", () => {
    for (const item of layout.furniture) expect(roomAt(layout, item)?.kind, item.id).toBe(item.room);
  });

  it("has exactly one cabinet per game, turned by quarter turns", () => {
    expect(cabinets).toHaveLength(ARCADE_GAMES.length);
    for (const game of ARCADE_GAMES) {
      expect(cabinets.filter((c) => c.id === cabinetId(game.id))).toHaveLength(1);
    }
    for (const cabinet of cabinets) {
      expect(gameForCabinet(cabinet.id)).not.toBeNull();
      const quarters = cabinet.rotationY / (Math.PI / 2);
      expect(Math.abs(quarters - Math.round(quarters))).toBeLessThan(1e-9);
    }
    expect(new Set(layout.furniture.map((f) => f.id)).size).toBe(layout.furniture.length);
  });

  it("turns every cabinet's screen towards the camera (south or east)", () => {
    for (const cabinet of cabinets) {
      const facing = { x: Math.round(Math.sin(cabinet.rotationY)), z: Math.round(Math.cos(cabinet.rotationY)) };
      expect(facing.x >= 0 && facing.z >= 0, cabinet.id).toBe(true);
    }
  });

  it("keeps solid furniture, decor and arch posts apart and inside the slab", () => {
    const solids = layout.furniture.filter((f) => FURNITURE_SIZE[f.kind].solid).map((f) => ({ id: f.id, rect: footprint(f) }));
    const tables = arcadeDecor(layout.rooms).tables.map((t, i) => ({
      id: `table-${i}`, rect: { minX: t.x - SNACK_TABLE_HALF, maxX: t.x + SNACK_TABLE_HALF, minZ: t.z - SNACK_TABLE_HALF, maxZ: t.z + SNACK_TABLE_HALF },
    }));
    const posts = layout.rooms.flatMap(archPosts).map((p, i) => ({
      id: `post-${i}`, rect: { minX: p.x - ARCH.post / 2, maxX: p.x + ARCH.post / 2, minZ: p.z - ARCH.post / 2, maxZ: p.z + ARCH.post / 2 },
    }));
    const walls = layout.walls.map((w, i) => ({ id: `wall-${i}`, rect: wallRect(w) }));
    const all = [...solids, ...tables, ...posts, ...walls];
    expect(tables).toHaveLength(3);
    for (let i = 0; i < all.length; i += 1) {
      expect(inside(all[i].rect, layout.bounds), all[i].id).toBe(true);
      for (let j = i + 1; j < all.length; j += 1) {
        expect(overlaps(all[i].rect, all[j].rect), `${all[i].id} × ${all[j].id}`).toBe(false);
      }
    }
    // Navigation walks round every one of them.
    for (const { rect } of all) expect(layout.obstacles).toContainEqual(rect);
  });

  it("can reach every cabinet's play spot from the elevator", () => {
    const pose = arrivalPose(layout, "elevator");
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

  it("keeps the elevator stop clear of the cabinets", () => {
    const stop = layout.checkpoints[0];
    expect(stop.room).toBe("snack");
    for (const cabinet of cabinets) {
      const spot = cabinetPlaySpot(cabinet);
      expect(Math.hypot(spot.x - stop.x, spot.z - stop.z)).toBeGreaterThan(stop.radius + 1);
    }
  });

  it("reaches the counters, the claw machines and the snack tables on foot", () => {
    const pose = arrivalPose(layout, "elevator");
    const front = (id: string) => {
      const item = layout.furniture.find((f) => f.id === id)!;
      const reach = FURNITURE_SIZE[item.kind].d / 2 + 0.5;
      return { x: item.x + Math.sin(item.rotationY) * reach, z: item.z + Math.cos(item.rotationY) * reach };
    };
    for (const id of ["prize-counter", "token-machine", "claw-a", "claw-b", "snack-counter", "air-hockey", "pinball-1"]) {
      expect(isWalkable(grid, front(id)), id).toBe(true);
      expect(findPath(grid, pose, front(id)), id).not.toBeNull();
    }
  });
});
