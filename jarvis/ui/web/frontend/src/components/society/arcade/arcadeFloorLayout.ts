/**
 * The arcade floor as data: the top floor of the building, one elevator ride
 * above the coding floor. It reads like the floors below it: a strip of
 * walled rooms in the north, an open hall in the middle and a strip of
 * walled rooms in the south, each room with its own door, sign, carpet and
 * neon colour, so the person always knows where they are.
 *
 *   ┌──────────── Classics ────────────┬──── Puzzle corner ────┬──────────── Action zone ───────────┐
 *   │  4 cabinets on the back wall     │  3 cabinets           │  3 cabinets                         │
 *   └───────────────[door]─────────────┴────────[door]─────────┴──────────────[door]───────────────┘
 *       pinballs        ·  pinball row  ·   ✪ floor logo   ·  air hockey   ·     dance floor          ← the hall
 *   ┌───────────────[door]─────────────┬────────[door]─────────┬──────────────[door]───────────────┐
 *   │ Foyer: elevator · token machines │ Prize shop: counter,  │ Snack bar: counter, bistro tables   │
 *   │        welcome logo              │ claw machines         │                                     │
 *   └──────────────────────────────────┴───────────────────────┴─────────────────────────────────────┘
 *
 * The doors of the north and south rooms line up on three lanes across the
 * hall, and nothing in the hall stands on a lane. Every cabinet stands on a
 * game room's back wall with its screen to the south, towards the camera and
 * the door. The walls are smoked glass (ArcadeHall), so no camera angle ever
 * hides a room or makes a wall pop away.
 *
 * Pure and deterministic, like officeLayout: the plan never depends on who is
 * online (nobody works up here: no departments, desks or hangout spots).
 * Units are metres, +x east, +z south, origin at the floor centre.
 */
import { cabinetId, type ArcadeGameId } from "./arcadeGames";
import {
  FURNITURE_SIZE, footprint, wallRect, wallsOf,
  type Checkpoint, type Furniture, type FurnitureKind, type OfficeLayout, type Point, type Rect, type Room, type RoomKind, type WallSegment,
} from "../office/officeLayout";

/** The building's footprint: rooms and hall fill ±FLOOR_X × ±FLOOR_Z; a ledge of EDGE runs round it to the railing. */
const FLOOR_X = 12;
const FLOOR_Z = 9;
const EDGE = 1.2;
/** How far a figure's centre stays from the slab edge (see officeLayout's RAIL_CLEARANCE). */
const RAIL_CLEARANCE = 0.65;
/** The x where the three columns of rooms meet; the middle column is the narrower one. */
const COLUMN_SPLIT = 3.6;
/** The north strip (game rooms) ends here; the south strip (foyer, prize shop, snack bar) starts here. */
const NORTH_END = -3.6;
const SOUTH_START = 2.6;
/** Door widths: game rooms, and the wider foyer door everyone arriving walks through. */
const DOOR = 1.8;
const FOYER_DOOR = 2.4;
/** Wall thickness used by officeLayout's wallRect; furniture stands this far plus a gap from a wall line. */
const WALL_GAP = 0.08;
/** Centre-to-centre pitch of cabinets standing side by side on a back wall. */
const CABINET_PITCH = 1.3;

/** A cabinet is played standing this far in front of it, along its local +z (OfficePlayer). */
export const CABINET_PLAY_DISTANCE = 0.85;

/** The bistro tables of the snack bar: table and stools fit inside a square of this half size. */
export const SNACK_TABLE_HALF = 0.62;

/** Which games stand in which game room, west to east along its back wall. */
export const GAME_ROOMS: Readonly<Record<"classics" | "puzzle" | "action", readonly ArcadeGameId[]>> = {
  classics: ["pixel-raiders", "maze-muncher", "city-defense", "paddle-duel"],
  puzzle: ["block-drop", "neon-snake", "brick-breaker"],
  action: ["asteroid-run", "desert-dash", "road-hopper"],
};

/** Each room's neon colour: its wall caps, its sign, its floor logo. */
export const ROOM_ACCENT: Partial<Record<RoomKind, string>> = {
  classics: "#a77bff",
  puzzle: "#2de2e6",
  action: "#ff8a3d",
  arcade: "#ff3fa4",
  foyer: "#ff3fa4",
  prizes: "#ffd23f",
  snack: "#ff5a6e",
};

/** Floor decor drawn by ArcadeHall but placed here, so navigation knows the solid parts. */
export interface ArcadeDecor {
  /** Bistro tables (with their stools) in the snack bar; solid. */
  tables: Point[];
  /** The light-up dance floor at the hall's east end; flat, walkable. */
  pad: Rect;
  /** Neon logos inlaid in the floors: one per game room, the foyer's welcome logo and the hall's centrepiece; flat, walkable. */
  logos: (Point & { r: number; room: RoomKind })[];
}

/** The building's column x-ranges, west to east, shared by the north and the south strip. */
function columns(): [number, number][] {
  return [[-FLOOR_X, -COLUMN_SPLIT], [-COLUMN_SPLIT, COLUMN_SPLIT], [COLUMN_SPLIT, FLOOR_X]];
}

const centreX = (r: Rect) => (r.minX + r.maxX) / 2;
const centreZ = (r: Rect) => (r.minZ + r.maxZ) / 2;

/** Where the floor decor lies, from the rooms of an arcade layout (missing rooms leave their decor out). */
export function arcadeDecor(rooms: readonly Room[]): ArcadeDecor {
  const room = (kind: RoomKind) => rooms.find((r) => r.kind === kind) ?? null;
  const snack = room("snack"), hall = room("arcade");
  const tables: Point[] = snack ? [
    { x: snack.minX + 3.3, z: snack.minZ + 2.5 }, { x: snack.minX + 6.1, z: snack.minZ + 2.5 },
    { x: snack.minX + 3.3, z: snack.minZ + 4.9 }, { x: snack.minX + 6.1, z: snack.minZ + 4.9 },
  ] : [];
  const pad: Rect = hall
    ? { minX: hall.maxX - 2.6, maxX: hall.maxX - 0.4, minZ: centreZ(hall) - 1.1, maxZ: centreZ(hall) + 1.1 }
    : { minX: 0, maxX: 0, minZ: 0, maxZ: 0 };
  const logos: ArcadeDecor["logos"] = [];
  for (const kind of ["classics", "puzzle", "action"] as const) {
    const r = room(kind);
    if (r) logos.push({ x: centreX(r), z: r.maxZ - 1.9, r: 1.05, room: kind });
  }
  const foyer = room("foyer");
  if (foyer) logos.push({ x: centreX(foyer) + 0.6, z: centreZ(foyer) + 0.3, r: 1.3, room: "foyer" });
  if (hall) logos.push({ x: centreX(hall), z: centreZ(hall), r: 1.25, room: "arcade" });
  return { tables, pad, logos };
}

/** The spot in front of a cabinet where the person stands to play it. */
export function cabinetPlaySpot(item: Pick<Furniture, "x" | "z" | "rotationY">): Point {
  return { x: item.x + Math.sin(item.rotationY) * CABINET_PLAY_DISTANCE, z: item.z + Math.cos(item.rotationY) * CABINET_PLAY_DISTANCE };
}

/** Wall runs of all rooms, each run once: two rooms sharing a wall line would otherwise both draw (and both block) it. */
export function uniqueWalls(rooms: readonly Room[]): WallSegment[] {
  const seen = new Set<string>();
  const out: WallSegment[] = [];
  for (const wall of rooms.flatMap(wallsOf)) {
    const [a, b] = [`${wall.x1.toFixed(3)},${wall.z1.toFixed(3)}`, `${wall.x2.toFixed(3)},${wall.z2.toFixed(3)}`].sort();
    const key = `${a}|${b}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(wall);
  }
  return out;
}

/** Build the arcade floor. Takes no input: the hall is the same for everyone. */
export function buildArcadeLayout(): OfficeLayout {
  const [west, middle, east] = columns();
  const north = -FLOOR_Z, south = FLOOR_Z;
  const walled = (kind: RoomKind, [minX, maxX]: [number, number], minZ: number, maxZ: number, doorSide: "north" | "south", width = DOOR): Room =>
    ({ id: kind, kind, walled: true, minX, maxX, minZ, maxZ, doors: [{ side: doorSide, at: (minX + maxX) / 2, width }] });

  const classics = walled("classics", west, north, NORTH_END, "south");
  const puzzle = walled("puzzle", middle, north, NORTH_END, "south");
  const action = walled("action", east, north, NORTH_END, "south");
  const hall: Room = { id: "arcade", kind: "arcade", walled: false, doors: [], minX: -FLOOR_X, maxX: FLOOR_X, minZ: NORTH_END, maxZ: SOUTH_START };
  const foyer = walled("foyer", west, SOUTH_START, south, "north", FOYER_DOOR);
  const prizes = walled("prizes", middle, SOUTH_START, south, "north");
  const snack = walled("snack", east, SOUTH_START, south, "north");
  const rooms = [classics, puzzle, action, hall, foyer, prizes, snack];

  const piece = (id: string, kind: FurnitureKind, room: RoomKind, x: number, z: number, rotationY = 0): Furniture =>
    ({ id, kind, x, z, rotationY, room });
  /** Centre offset of a piece standing with its back against a wall (its depth along the wall's normal). */
  const off = (kind: FurnitureKind) => WALL_GAP + FURNITURE_SIZE[kind].d / 2;

  // The cabinets: each game room's games side by side on its back (north) wall, screens to the south.
  const cabinets: Furniture[] = [];
  for (const room of [classics, puzzle, action]) {
    const games = GAME_ROOMS[room.kind as keyof typeof GAME_ROOMS];
    games.forEach((game, i) => {
      const x = centreX(room) + (i - (games.length - 1) / 2) * CABINET_PITCH;
      cabinets.push(piece(cabinetId(game), "retroCabinet", room.kind, x, room.minZ + off("retroCabinet")));
    });
  }

  // The foyer: the elevator on the west wall like on every floor (doors facing east), the token machines north of it.
  const elevator = piece("elevator", "elevator", "foyer", foyer.minX + off("elevator"), foyer.maxZ - 2.8, Math.PI / 2);
  const tokens = [0, 1].map((i) => piece(`token-${i}`, "tokenMachine", "foyer", foyer.minX + off("tokenMachine"), foyer.minZ + 1.0 + i * 0.8, Math.PI / 2));
  // Two big plants soften the foyer's east corners.
  const plants = [foyer.minZ + 0.5, foyer.maxZ - 0.5].map((z, i) => piece(`foyer-plant-${i}`, "plant", "foyer", foyer.maxX - 0.5, z));

  // The prize shop: the counter on its west wall facing the room, a claw machine either side of the door and one on the east wall.
  const counterZ = centreZ(prizes) + 0.4;
  const prizeFurniture = [
    piece("prize-counter", "prizeCounter", "prizes", prizes.minX + off("prizeCounter"), counterZ, Math.PI / 2),
    piece("claw-a", "clawMachine", "prizes", centreX(prizes) - 2.0, prizes.minZ + off("clawMachine")),
    piece("claw-b", "clawMachine", "prizes", centreX(prizes) + 2.0, prizes.minZ + off("clawMachine")),
    piece("claw-c", "clawMachine", "prizes", prizes.maxX - off("clawMachine"), counterZ + 0.6, -Math.PI / 2),
  ];

  // The snack bar: its counter on the west wall facing the tables.
  const snackCounter = piece("snack-counter", "snackCounter", "snack", snack.minX + off("snackCounter"), centreZ(snack) + 0.3, Math.PI / 2);

  // The hall: a pinball pair at the west end and a row of three west of the middle lane, the air-hockey table east of it.
  // The three lanes (x of the doors) stay clear.
  const mid = centreZ(hall);
  const pinballZ = mid - 0.4;
  const pinballs = [-11.0, -10.1, -5.4, -4.5, -3.6].map((x, i) => piece(`pinball-${i}`, "pinball", "arcade", x, pinballZ));
  const hockey = piece("air-hockey", "airHockey", "arcade", 4.3, mid);

  const furniture: Furniture[] = [...cabinets, elevator, ...tokens, ...plants, ...prizeFurniture, snackCounter, ...pinballs, hockey];

  // The elevator's doors face east into the foyer; its stop is the floor in front of them.
  const elevatorStop: Checkpoint = { id: "elevator", room: "foyer", x: elevator.x + 0.95, z: elevator.z, radius: 1.0 };
  const decor = arcadeDecor(rooms);
  const walls = uniqueWalls(rooms);
  const obstacles: Rect[] = [
    ...walls.map(wallRect),
    ...furniture.filter((f) => FURNITURE_SIZE[f.kind].solid).map(footprint),
    ...decor.tables.map((t) => ({ minX: t.x - SNACK_TABLE_HALF, maxX: t.x + SNACK_TABLE_HALF, minZ: t.z - SNACK_TABLE_HALF, maxZ: t.z + SNACK_TABLE_HALF })),
  ];

  const bounds: Rect = { minX: -FLOOR_X - EDGE, maxX: FLOOR_X + EDGE, minZ: -FLOOR_Z - EDGE, maxZ: FLOOR_Z + EDGE };
  return {
    variant: "arcade",
    departments: [],
    // Nobody leads up here; the rect is the foyer so it stays a real place on the floor.
    lead: { minX: foyer.minX, maxX: foyer.maxX, minZ: foyer.minZ, maxZ: foyer.maxZ, desks: [] },
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

/** The three lanes across the hall: the x of the doors that face each other across it. */
export function hallLanes(layout: Pick<OfficeLayout, "rooms">): number[] {
  return layout.rooms.filter((r) => r.walled && r.maxZ <= NORTH_END + 1e-6).map((r) => r.doors[0].at);
}
