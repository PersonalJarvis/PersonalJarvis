/**
 * The arcade floor as data: the top floor of the building, one elevator ride
 * above the coding floor. A dark, neon-lit 80s/90s arcade hall with every
 * playable cabinet of the registry, a pinball row, an air-hockey table, a
 * prize counter with claw machines and a token changer, and a snack bar by
 * the elevator.
 *
 * Pure and deterministic, like officeLayout: the plan never depends on who is
 * online (nobody works up here, so it has no departments, desks or hangout
 * spots). Units are metres, +x east, +z south, origin at the floor centre.
 * The camera looks from the south-east, so the floor is a diorama with two
 * back walls (north and west, drawn by ArcadeHall) and glass railings on the
 * south and east; tall pieces stand along the back walls, every cabinet's
 * screen faces south or east, towards the camera.
 *
 *   ┌───────── north wall ─────────────────────────────────────────────┐
 *   │ PRIZES: counter · tokens   │ pinballs · cabinet row A · dance pad │
 *   │ claw machines (west wall)  │ row B (faces east) · island C        │
 *   │ SNACK: counter · tables    │                                      │
 *   │ elevator (west wall)       │              air hockey              │
 *   └──────────────────────────────────────────────────────── railing ─┘
 *
 * Open rooms only: each one's name arch stands on its north edge, right
 * behind the counter (prizes, snack) or the cabinet row (hall), so the sign
 * reads as the counter's own gantry.
 */
import { ARCADE_GAMES, cabinetId } from "./arcadeGames";
import {
  archPosts, ARCH, FURNITURE_SIZE, footprint, wallRect, wallsOf,
  type Checkpoint, type Furniture, type FurnitureKind, type OfficeLayout, type Point, type Rect, type Room, type RoomKind,
} from "../office/officeLayout";

/** Half the slab's size (bounds = ±HALF_W × ±HALF_D). */
const HALF_W = 11.5;
const HALF_D = 8.5;
/** The back walls and the railings stand this far in from the slab's edge. */
export const ARCADE_WALL_INSET = 0.2;
/** How far a figure's centre stays from the slab edge (see officeLayout's RAIL_CLEARANCE). */
const RAIL_CLEARANCE = 0.65;
/** Width of the west column holding the prize corner (north) and the snack bar (south). */
const WEST_COLUMN = 5.8;
/** Where the prize corner ends and the snack bar begins. */
const PRIZES_DEPTH = 5.8;
/** Centre-to-centre pitch of cabinets standing side by side (a 0.8 m cabinet and a 0.3 m gap). */
const CABINET_PITCH = 1.1;
/** The arch posts stand right behind a counter or a cabinet row; this keeps them clear of it. */
const BEHIND_ARCH = ARCH.post / 2 + 0.06;

/** A cabinet is played standing this far in front of it, along its local +z (OfficePlayer). */
export const CABINET_PLAY_DISTANCE = 0.85;

/** The bistro tables of the snack bar: table and stools fit inside a square of this half size. */
export const SNACK_TABLE_HALF = 0.62;

/** Floor decor that is drawn by ArcadeHall but placed here, so navigation knows the solid parts. */
export interface ArcadeDecor {
  /** Bistro tables (with their stools) in the snack bar; solid. */
  tables: Point[];
  /** The light-up dance pad in the hall's north-east corner; flat, walkable. */
  pad: Rect;
  /** A neon star medallion inlaid in the carpet in the open south of the hall; flat, walkable. */
  medallion: Point & { r: number };
}

/** The snack bar's three bistro tables, south of its counter, clear of the elevator stop. */
function snackTables(room: Rect): Point[] {
  const cx = (room.minX + room.maxX) / 2;
  return [
    { x: cx - 1.2, z: room.minZ + 3.4 },
    { x: cx + 1.2, z: room.minZ + 3.4 },
    { x: cx, z: room.minZ + 5.6 },
  ];
}

/** Where the floor decor lies, from the rooms of an arcade layout (empty when a room is missing). */
export function arcadeDecor(rooms: readonly Room[]): ArcadeDecor {
  const snack = rooms.find((r) => r.kind === "snack");
  const hall = rooms.find((r) => r.kind === "arcade");
  return {
    tables: snack ? snackTables(snack) : [],
    pad: hall
      ? { minX: hall.maxX - 3.9, maxX: hall.maxX - 1.5, minZ: hall.minZ + 0.9, maxZ: hall.minZ + 3.3 }
      : { minX: 0, maxX: 0, minZ: 0, maxZ: 0 },
    medallion: hall ? { x: hall.minX + 6.6, z: hall.maxZ - 3.6, r: 1.5 } : { x: 0, z: 0, r: 0 },
  };
}

/** The spot in front of a cabinet where the person stands to play it. */
export function cabinetPlaySpot(item: Pick<Furniture, "x" | "z" | "rotationY">): Point {
  return { x: item.x + Math.sin(item.rotationY) * CABINET_PLAY_DISTANCE, z: item.z + Math.cos(item.rotationY) * CABINET_PLAY_DISTANCE };
}

/** Build the arcade floor. Takes no input: the hall is the same for everyone. */
export function buildArcadeLayout(): OfficeLayout {
  // The lines of the back walls (north, west) and the railings (south, east).
  const west = -HALF_W + ARCADE_WALL_INSET, east = HALF_W - ARCADE_WALL_INSET;
  const north = -HALF_D + ARCADE_WALL_INSET, south = HALF_D - ARCADE_WALL_INSET;
  const split = west + WEST_COLUMN;

  const prizes: Room = { id: "prizes", kind: "prizes", walled: false, doors: [], minX: west, maxX: split, minZ: north, maxZ: north + PRIZES_DEPTH };
  const snack: Room = { id: "snack", kind: "snack", walled: false, doors: [], minX: west, maxX: split, minZ: prizes.maxZ, maxZ: south };
  const hall: Room = { id: "arcade", kind: "arcade", walled: false, doors: [], minX: split, maxX: east, minZ: north, maxZ: south };
  const rooms = [hall, prizes, snack];

  const piece = (id: string, kind: FurnitureKind, room: RoomKind, x: number, z: number, rotationY = 0): Furniture =>
    ({ id, kind, x, z, rotationY, room });
  /** z of a piece facing south whose back stands just in front of its room's name arch. */
  const underArch = (room: Room, kind: FurnitureKind) => archPosts(room)[0].z + BEHIND_ARCH + FURNITURE_SIZE[kind].d / 2;
  /** z of a piece facing south with its back against the north wall. */
  const againstNorth = (kind: FurnitureKind) => north + 0.08 + FURNITURE_SIZE[kind].d / 2;
  /** x of a piece turned to face east with its back against the west wall. */
  const againstWest = (kind: FurnitureKind) => west + 0.08 + FURNITURE_SIZE[kind].d / 2;
  const pcx = (prizes.minX + prizes.maxX) / 2, scx = (snack.minX + snack.maxX) / 2, hcx = (hall.minX + hall.maxX) / 2;

  // The cabinets, in registry order: row A along the north wall under the hall's
  // arch, row B down the hall's west side facing east, island C in the middle.
  const slots: { x: number; z: number; rotationY: number }[] = [
    ...[0, 1, 2, 3].map((i) => ({ x: hcx + (i - 1.5) * CABINET_PITCH, z: underArch(hall, "retroCabinet"), rotationY: 0 })),
    ...[0, 1, 2].map((i) => ({ x: split + 1.2, z: north + 4.9 + i * CABINET_PITCH, rotationY: Math.PI / 2 })),
    ...[0, 1, 2].map((i) => ({ x: hcx + (i - 1) * CABINET_PITCH - 1.1, z: north + 5.9, rotationY: 0 })),
  ];
  const cabinets: Furniture[] = ARCADE_GAMES.map((game, i) => {
    const slot = slots[i % slots.length];
    return piece(cabinetId(game.id), "retroCabinet", "arcade", slot.x, slot.z, slot.rotationY);
  });

  const elevatorZ = south - 2.7;
  const elevator = piece("elevator", "elevator", "snack", againstWest("elevator"), elevatorZ, Math.PI / 2);
  const furniture: Furniture[] = [
    ...cabinets,
    // Prize corner: the counter under its arch, the token changer beside it, two claw machines on the west wall.
    piece("prize-counter", "prizeCounter", "prizes", pcx, underArch(prizes, "prizeCounter")),
    piece("token-machine", "tokenMachine", "prizes", prizes.maxX - 0.55, againstNorth("tokenMachine") + 0.2),
    piece("claw-a", "clawMachine", "prizes", againstWest("clawMachine"), prizes.minZ + 2.6, Math.PI / 2),
    piece("claw-b", "clawMachine", "prizes", againstWest("clawMachine"), prizes.minZ + 3.8, Math.PI / 2),
    // Snack bar: the counter under its arch; the elevator on the west wall.
    piece("snack-counter", "snackCounter", "snack", scx, underArch(snack, "snackCounter")),
    elevator,
    // Hall: a pinball row on the north wall west of row A, the air-hockey table in the open south-east.
    ...[0, 1, 2].map((i) => piece(`pinball-${i}`, "pinball", "arcade", split + 1.9 + i * 0.85, againstNorth("pinball"))),
    piece("air-hockey", "airHockey", "arcade", east - 3.7, south - 4.7),
  ];

  // The elevator's doors face east into the snack bar; its stop is the floor in front of them.
  const elevatorStop: Checkpoint = { id: "elevator", room: "snack", x: elevator.x + 0.95, z: elevator.z, radius: 1.0 };
  const decor = arcadeDecor(rooms);
  const walls = rooms.flatMap(wallsOf);
  const obstacles: Rect[] = [
    ...walls.map(wallRect),
    ...furniture.filter((f) => FURNITURE_SIZE[f.kind].solid).map(footprint),
    ...decor.tables.map((t) => ({ minX: t.x - SNACK_TABLE_HALF, maxX: t.x + SNACK_TABLE_HALF, minZ: t.z - SNACK_TABLE_HALF, maxZ: t.z + SNACK_TABLE_HALF })),
    // The posts of the open rooms' name arches.
    ...rooms.filter((r) => !r.walled).flatMap(archPosts).map((p) => ({
      minX: p.x - ARCH.post / 2, maxX: p.x + ARCH.post / 2, minZ: p.z - ARCH.post / 2, maxZ: p.z + ARCH.post / 2,
    })),
  ];

  const bounds: Rect = { minX: -HALF_W, maxX: HALF_W, minZ: -HALF_D, maxZ: HALF_D };
  return {
    variant: "arcade",
    departments: [],
    // Nobody leads up here; the rect is the prize corner so it stays a real place on the floor.
    lead: { minX: prizes.minX, maxX: prizes.maxX, minZ: prizes.minZ, maxZ: prizes.maxZ, desks: [] },
    rooms,
    walls,
    furniture,
    spots: [],
    checkpoints: [elevatorStop],
    obstacles,
    spawn: { x: elevator.x + 1.15, z: elevator.z },
    arrival: { x: elevator.x + 1.6, z: elevator.z },
    floor: {
      minX: bounds.minX + RAIL_CLEARANCE, maxX: bounds.maxX - RAIL_CLEARANCE,
      minZ: bounds.minZ + RAIL_CLEARANCE, maxZ: bounds.maxZ - RAIL_CLEARANCE,
    },
    bounds,
  };
}
