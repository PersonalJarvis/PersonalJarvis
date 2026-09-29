/**
 * The office floor as data: rooms, departments, desks, furniture, hangout
 * spots, checkpoints and the obstacles that navigation walks around.
 *
 * Pure and deterministic — the same roster always produces the same floor and
 * seating, so an agent never "moves desks" on a refetch. Units are metres,
 * +x east, +z south, origin at the floor centre. The camera looks from the
 * south-east, so tall rooms stand in the north and the lobby lies in front.
 *
 *   ┌──────────── north strip: Lead office · Team room · Wardrobe ───────────┐
 *   │                   departments (open office, 2 columns)                 │
 *   └──── south strip: Reception + lobby (Agent board) · Break room ─────────┘
 */
import type { AgentRunState, AgentTier } from "../data";

export interface OfficeAgentInput {
  agentId: string;
  name: string;
  tier: AgentTier;
  providerLabel: string;
  state: AgentRunState;
  createdMs: number;
}

export interface Rect { minX: number; maxX: number; minZ: number; maxZ: number }
export interface Point { x: number; z: number }

export type Facing = "north" | "south";

export interface DeskSlot {
  id: string;
  /** Desk centre on the floor. */
  x: number;
  z: number;
  /** The direction the seated agent looks (towards its monitor). */
  facing: Facing;
  agentId: string | null;
}

export interface Department extends Rect {
  id: string;
  /** Provider family shown on the sign; "" for a spare open-space department. */
  label: string;
  desks: DeskSlot[];
  /** Carpet tint index into the palette's department colours. */
  tint: number;
}

export type RoomKind = "lead" | "team" | "wardrobe" | "reception" | "break";

export interface Door { side: "north" | "south" | "east" | "west"; /** Centre of the gap along the wall. */ at: number; width: number }

export interface Room extends Rect {
  id: RoomKind;
  kind: RoomKind;
  /** Walled rooms get glass walls on every side except their doors; open rooms have none. */
  walled: boolean;
  doors: Door[];
}

export interface WallSegment { x1: number; z1: number; x2: number; z2: number; room: RoomKind }

export type CheckpointKind = "create" | "manage" | "team" | "wardrobe" | "lead" | "break";

/** A place the person can walk to (or click from afar) to act. */
export interface Checkpoint extends Point { id: CheckpointKind; room: RoomKind; /** Walk-in radius in metres. */ radius: number }

export type FurnitureKind =
  | "meetingTable" | "teamBoard" | "receptionDesk" | "kiosk" | "lockers" | "mirror"
  | "coffeeBar" | "waterCooler" | "couch" | "coffeeTable" | "arcade" | "beanbag"
  | "bookshelf" | "plant" | "rug" | "elevator";

/**
 * Footprint (x-extent × z-extent before rotation) and height of each piece.
 * The renderer MUST build every prop inside this box: navigation reads the
 * same numbers, so a prop larger than its footprint makes figures clip into it.
 */
export const FURNITURE_SIZE: Record<FurnitureKind, { w: number; d: number; h: number; solid: boolean }> = {
  meetingTable: { w: 3.6, d: 1.6, h: 0.76, solid: true },
  teamBoard: { w: 2.6, d: 0.2, h: 1.9, solid: true },
  receptionDesk: { w: 2.8, d: 0.9, h: 1.1, solid: true },
  kiosk: { w: 1.0, d: 0.5, h: 1.8, solid: true },
  lockers: { w: 2.4, d: 0.5, h: 1.9, solid: true },
  mirror: { w: 0.9, d: 0.12, h: 1.9, solid: true },
  coffeeBar: { w: 2.4, d: 0.7, h: 1.05, solid: true },
  waterCooler: { w: 0.45, d: 0.45, h: 1.3, solid: true },
  couch: { w: 2.2, d: 0.9, h: 0.85, solid: true },
  coffeeTable: { w: 1.1, d: 1.1, h: 0.4, solid: true },
  arcade: { w: 0.8, d: 0.8, h: 1.8, solid: true },
  beanbag: { w: 0.9, d: 0.9, h: 0.6, solid: true },
  bookshelf: { w: 1.8, d: 0.36, h: 1.4, solid: true },
  plant: { w: 0.6, d: 0.6, h: 1.3, solid: true },
  rug: { w: 1, d: 1, h: 0.02, solid: false },
  elevator: { w: 2.2, d: 0.4, h: 2.4, solid: true },
};

export interface Furniture extends Point {
  id: string;
  kind: FurnitureKind;
  /** Rotation about +y. 0 = the prop's front faces +z (south, towards the camera). */
  rotationY: number;
  room: RoomKind | "floor";
  /** Only rugs are sized per instance (w × d); everything else uses FURNITURE_SIZE. */
  size?: { w: number; d: number };
}

export type SpotKind = "couch" | "coffee" | "cooler" | "window" | "shelf" | "arcade" | "meeting" | "beanbag" | "board";
export type SpotPose = "sit" | "stand" | "sleep";

/** Where an idle agent (or the person) can hang out. `facing` is the figure's rotation about +y. */
export interface Spot extends Point { id: string; kind: SpotKind; pose: SpotPose; facing: number; room: RoomKind | "floor" }

export interface OfficeLayout {
  departments: Department[];
  /** Lead desks live in the lead office. */
  lead: Rect & { desks: DeskSlot[] };
  rooms: Room[];
  walls: WallSegment[];
  furniture: Furniture[];
  spots: Spot[];
  checkpoints: Checkpoint[];
  /** Solid footprints (desks, walls, furniture), un-inflated. Navigation inflates them by the walker radius. */
  obstacles: Rect[];
  /** Where the person's character starts. */
  spawn: Point;
  /** The walkable floor inside the railing. */
  floor: Rect;
  /** The whole slab, railing included. */
  bounds: Rect;
}

/** Desk pitch along a row and the depth of one bench (two rows back to back plus chairs). */
export const DESK_PITCH_X = 1.7;
export const BENCH_DEPTH_Z = 4.2;
/** Half the gap between the two monitors of a back-to-back pair. */
export const DESK_HALF_GAP = 0.45;
export const DESK_SIZE = { w: 1.5, d: 0.8 } as const;
/** Chair centre behind a desk, along the agent's back direction. */
export const SEAT_OFFSET = 0.62;
export const DESKS_PER_ROW = 4;
const SEATS_PER_BENCH = DESKS_PER_ROW * 2;
const MIN_BENCHES = 1;
const MAX_BENCHES = 4;
const DEPT_MARGIN_X = 1.1;
const DEPT_HEADER_Z = 1.8;
const DEPT_FOOTER_Z = 0.8;
const AISLE = 3.2;
const EDGE = 1.6;
const NORTH_DEPTH = 7;
const SOUTH_DEPTH = 8;
const COLUMNS = 2;
const WALL_THICKNESS = 0.12;
/** More departments than this fold into the last one — the floor stays one screen. */
export const MAX_DEPARTMENTS = 6;
/** The floor always shows at least this many departments; spare ones are open space with free desks. */
export const MIN_DEPARTMENTS = 4;
export const MAX_SEATED = MAX_DEPARTMENTS * MAX_BENCHES * SEATS_PER_BENCH;

/** The department an agent works in: its provider family, or Jarvis' own brain. */
export function departmentKey(agent: Pick<OfficeAgentInput, "providerLabel">): string {
  const label = agent.providerLabel.trim();
  return label.length > 0 ? label.charAt(0).toUpperCase() + label.slice(1) : "Jarvis";
}

function byArrival(a: OfficeAgentInput, b: OfficeAgentInput): number {
  return a.createdMs - b.createdMs || a.agentId.localeCompare(b.agentId);
}

/** Group staff (non-lead) agents into at most MAX_DEPARTMENTS stable departments. */
export function groupDepartments(agents: readonly OfficeAgentInput[]): { label: string; members: OfficeAgentInput[] }[] {
  const groups = new Map<string, OfficeAgentInput[]>();
  for (const agent of agents) {
    if (agent.tier === "lead") continue;
    const key = departmentKey(agent);
    const list = groups.get(key) ?? [];
    list.push(agent);
    groups.set(key, list);
  }
  const ordered = [...groups.entries()]
    .map(([label, members]) => ({ label, members: [...members].sort(byArrival) }))
    .sort((a, b) => b.members.length - a.members.length || a.label.localeCompare(b.label));
  if (ordered.length === 0) return [{ label: "Jarvis", members: [] }];
  if (ordered.length <= MAX_DEPARTMENTS) return ordered;
  const kept = ordered.slice(0, MAX_DEPARTMENTS - 1);
  const rest = ordered.slice(MAX_DEPARTMENTS - 1).flatMap((g) => g.members).sort(byArrival);
  return [...kept, { label: "Other", members: rest }];
}

function benchCount(members: number): number {
  return Math.min(MAX_BENCHES, Math.max(MIN_BENCHES, Math.ceil(members / SEATS_PER_BENCH)));
}

const DEPT_WIDTH = DESKS_PER_ROW * DESK_PITCH_X + DEPT_MARGIN_X * 2;

function deptDepth(benches: number): number {
  return DEPT_HEADER_Z + benches * BENCH_DEPTH_Z + DEPT_FOOTER_Z;
}

/** Desk slots of one department, seated in arrival order: bench by bench, front row first. */
function layoutDesks(deptId: string, minX: number, minZ: number, benches: number, members: OfficeAgentInput[]): DeskSlot[] {
  const desks: DeskSlot[] = [];
  let seat = 0;
  for (let bench = 0; bench < benches; bench += 1) {
    const centreZ = minZ + DEPT_HEADER_Z + bench * BENCH_DEPTH_Z + BENCH_DEPTH_Z / 2;
    for (const facing of ["north", "south"] as const) {
      // A north-facing agent sits on the south side of the pair, looking at its monitor.
      const z = facing === "north" ? centreZ + DESK_HALF_GAP : centreZ - DESK_HALF_GAP;
      for (let col = 0; col < DESKS_PER_ROW; col += 1) {
        const x = minX + DEPT_MARGIN_X + DESK_PITCH_X * (col + 0.5);
        desks.push({ id: `${deptId}:${bench}:${facing}:${col}`, x, z, facing, agentId: members[seat]?.agentId ?? null });
        seat += 1;
      }
    }
  }
  return desks;
}

/** The chair a desk's agent sits on, and the figure's heading while seated. */
export function seatOf(desk: Pick<DeskSlot, "x" | "z" | "facing">): Point & { facing: number } {
  return desk.facing === "north"
    ? { x: desk.x, z: desk.z + SEAT_OFFSET, facing: Math.PI }
    : { x: desk.x, z: desk.z - SEAT_OFFSET, facing: 0 };
}

/** Where an agent stands beside its chair (e.g. waiting for the person). */
export function standOf(desk: Pick<DeskSlot, "x" | "z" | "facing">): Point & { facing: number } {
  const seat = seatOf(desk);
  return { x: seat.x + 0.55, z: seat.z, facing: seat.facing };
}

/** Axis-aligned footprint of a rotated piece (rotations are multiples of 90°). */
export function footprint(item: Pick<Furniture, "x" | "z" | "kind" | "rotationY" | "size">): Rect {
  const base = item.size ?? FURNITURE_SIZE[item.kind];
  const quarter = Math.round(item.rotationY / (Math.PI / 2)) % 2 !== 0;
  const w = quarter ? base.d : base.w;
  const d = quarter ? base.w : base.d;
  return { minX: item.x - w / 2, maxX: item.x + w / 2, minZ: item.z - d / 2, maxZ: item.z + d / 2 };
}

function wallsOf(room: Room): WallSegment[] {
  if (!room.walled) return [];
  const sides: { side: Door["side"]; a: Point; b: Point }[] = [
    { side: "north", a: { x: room.minX, z: room.minZ }, b: { x: room.maxX, z: room.minZ } },
    { side: "south", a: { x: room.minX, z: room.maxZ }, b: { x: room.maxX, z: room.maxZ } },
    { side: "west", a: { x: room.minX, z: room.minZ }, b: { x: room.minX, z: room.maxZ } },
    { side: "east", a: { x: room.maxX, z: room.minZ }, b: { x: room.maxX, z: room.maxZ } },
  ];
  const out: WallSegment[] = [];
  for (const { side, a, b } of sides) {
    const horizontal = side === "north" || side === "south";
    const start = horizontal ? a.x : a.z;
    const end = horizontal ? b.x : b.z;
    const gaps = room.doors.filter((d) => d.side === side).map((d) => [d.at - d.width / 2, d.at + d.width / 2] as const)
      .sort((p, q) => p[0] - q[0]);
    let cursor = start;
    const push = (from: number, to: number) => {
      if (to - from < 0.05) return;
      out.push(horizontal
        ? { x1: from, z1: a.z, x2: to, z2: a.z, room: room.kind }
        : { x1: a.x, z1: from, x2: a.x, z2: to, room: room.kind });
    };
    for (const [g0, g1] of gaps) { push(cursor, Math.max(cursor, g0)); cursor = Math.max(cursor, g1); }
    push(cursor, end);
  }
  return out;
}

/** The free-standing name arch of an open (unwalled) room: two posts on its north edge. */
export const ARCH = { halfSpan: 0.95, inset: 0.25, post: 0.08 } as const;

/** Post centres of an open room's name arch; the renderer and navigation share them. */
export function archPosts(room: Rect): Point[] {
  const cx = (room.minX + room.maxX) / 2;
  const z = room.minZ + ARCH.inset;
  return [{ x: cx - ARCH.halfSpan, z }, { x: cx + ARCH.halfSpan, z }];
}

function wallRect(w: WallSegment): Rect {
  const t = WALL_THICKNESS / 2;
  return { minX: Math.min(w.x1, w.x2) - t, maxX: Math.max(w.x1, w.x2) + t, minZ: Math.min(w.z1, w.z2) - t, maxZ: Math.max(w.z1, w.z2) + t };
}

/** Build the floor for a roster. Seating and the whole floor plan are stable for an unchanged roster. */
export function buildOfficeLayout(agents: readonly OfficeAgentInput[]): OfficeLayout {
  const groups: { label: string; members: OfficeAgentInput[] }[] = groupDepartments(agents);
  if (groups.length === 1 && groups[0].members.length === 0) groups.length = 0;
  while (groups.length < MIN_DEPARTMENTS) groups.push({ label: "", members: [] });

  const floorWidth = COLUMNS * DEPT_WIDTH + (COLUMNS - 1) * AISLE;
  const minX = -floorWidth / 2;
  const maxX = floorWidth / 2;
  const rows = Math.ceil(groups.length / COLUMNS);
  const rowDepths: number[] = [];
  for (let row = 0; row < rows; row += 1) {
    const inRow = groups.slice(row * COLUMNS, row * COLUMNS + COLUMNS);
    rowDepths.push(Math.max(...inRow.map((g) => deptDepth(benchCount(g.members.length)))));
  }
  const floorDepth = NORTH_DEPTH + AISLE + rowDepths.reduce((s, d) => s + d, 0) + (rows - 1) * AISLE + AISLE + SOUTH_DEPTH;
  const topZ = -floorDepth / 2;
  const bottomZ = floorDepth / 2;

  // North strip: lead office · team room · wardrobe, each with a door facing the office.
  const leadW = 8.2, teamW = 8.4;
  const northMaxZ = topZ + NORTH_DEPTH;
  const leadRoom: Room = { id: "lead", kind: "lead", walled: true, minX, maxX: minX + leadW, minZ: topZ, maxZ: northMaxZ,
    doors: [{ side: "south", at: minX + leadW / 2, width: 1.6 }] };
  const teamRoom: Room = { id: "team", kind: "team", walled: true, minX: leadRoom.maxX, maxX: leadRoom.maxX + teamW, minZ: topZ, maxZ: northMaxZ,
    doors: [{ side: "south", at: leadRoom.maxX + teamW / 2, width: 1.8 }] };
  const wardrobeRoom: Room = { id: "wardrobe", kind: "wardrobe", walled: true, minX: teamRoom.maxX, maxX, minZ: topZ, maxZ: northMaxZ,
    doors: [{ side: "south", at: (teamRoom.maxX + maxX) / 2, width: 1.5 }] };

  // Departments between the strips.
  const departments: Department[] = [];
  let z = northMaxZ + AISLE;
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < COLUMNS; col += 1) {
      const index = row * COLUMNS + col;
      const group = groups[index];
      if (!group) continue;
      const benches = benchCount(group.members.length);
      const dMinX = minX + col * (DEPT_WIDTH + AISLE);
      const id = `dept-${index}`;
      departments.push({
        id, label: group.label, tint: index,
        minX: dMinX, maxX: dMinX + DEPT_WIDTH, minZ: z, maxZ: z + deptDepth(benches),
        desks: layoutDesks(id, dMinX, z, benches, group.members.slice(0, benches * SEATS_PER_BENCH)),
      });
    }
    z += rowDepths[row] + AISLE;
  }

  // South strip: an open reception/lobby in the west, a walled break room in the east.
  const southMinZ = bottomZ - SOUTH_DEPTH;
  const breakW = 10.5;
  const receptionRoom: Room = { id: "reception", kind: "reception", walled: false, minX, maxX: maxX - breakW, minZ: southMinZ, maxZ: bottomZ, doors: [] };
  const breakRoom: Room = { id: "break", kind: "break", walled: true, minX: maxX - breakW, maxX, minZ: southMinZ, maxZ: bottomZ,
    doors: [{ side: "north", at: maxX - breakW / 2, width: 2.2 }, { side: "west", at: southMinZ + SOUTH_DEPTH / 2, width: 2 }] };
  const rooms = [leadRoom, teamRoom, wardrobeRoom, receptionRoom, breakRoom];

  // Lead desks face south, towards the door and the office.
  const leadAgents = agents.filter((a) => a.tier === "lead").sort(byArrival).slice(0, 2);
  const lead = {
    minX: leadRoom.minX, maxX: leadRoom.maxX, minZ: leadRoom.minZ, maxZ: leadRoom.maxZ,
    desks: [0, 1].map((i): DeskSlot => ({
      id: `lead:${i}`, x: minX + leadW * (i === 0 ? 0.3 : 0.7), z: topZ + 2.6, facing: "south", agentId: leadAgents[i]?.agentId ?? null,
    })),
  };

  const tcx = (teamRoom.minX + teamRoom.maxX) / 2, tcz = (teamRoom.minZ + teamRoom.maxZ) / 2;
  const wcx = (wardrobeRoom.minX + wardrobeRoom.maxX) / 2;
  const bx0 = breakRoom.minX, bz0 = breakRoom.minZ;
  const rx0 = receptionRoom.minX;
  const furniture: Furniture[] = [
    // Lead office.
    { id: "lead-shelf", kind: "bookshelf", x: leadRoom.maxX - 1.3, z: topZ + 0.35, rotationY: 0, room: "lead" },
    { id: "lead-plant", kind: "plant", x: minX + 0.6, z: northMaxZ - 0.6, rotationY: 0, room: "lead" },
    // Team room: one long table, a board on the north wall.
    { id: "team-table", kind: "meetingTable", x: tcx, z: tcz + 0.4, rotationY: 0, room: "team" },
    { id: "team-board", kind: "teamBoard", x: tcx, z: topZ + 0.25, rotationY: 0, room: "team" },
    { id: "team-plant", kind: "plant", x: teamRoom.maxX - 0.6, z: northMaxZ - 0.6, rotationY: 0, room: "team" },
    // Wardrobe: lockers and a mirror.
    { id: "wardrobe-lockers", kind: "lockers", x: wcx - 0.2, z: topZ + 0.4, rotationY: 0, room: "wardrobe" },
    { id: "wardrobe-mirror", kind: "mirror", x: wardrobeRoom.maxX - 0.25, z: tcz, rotationY: -Math.PI / 2, room: "wardrobe" },
    // Reception and lobby.
    { id: "elevator", kind: "elevator", x: rx0 + 0.25, z: bottomZ - 2.6, rotationY: Math.PI / 2, room: "reception" },
    { id: "reception-desk", kind: "receptionDesk", x: rx0 + 3.2, z: southMinZ + 2.6, rotationY: 0, room: "reception" },
    { id: "kiosk", kind: "kiosk", x: receptionRoom.maxX - 2.2, z: southMinZ + 2.2, rotationY: 0, room: "reception" },
    { id: "lobby-plant-a", kind: "plant", x: rx0 + 0.6, z: southMinZ + 0.8, rotationY: 0, room: "reception" },
    { id: "lobby-plant-b", kind: "plant", x: receptionRoom.maxX - 0.8, z: bottomZ - 0.7, rotationY: 0, room: "reception" },
    // Break room: couches around a table, coffee bar, water cooler, arcade, beanbags.
    { id: "break-rug", kind: "rug", x: bx0 + 3.6, z: bz0 + 4.6, rotationY: 0, room: "break", size: { w: 5.6, d: 4.2 } },
    { id: "break-couch-a", kind: "couch", x: bx0 + 3.6, z: bz0 + 6.3, rotationY: Math.PI, room: "break" },
    { id: "break-couch-b", kind: "couch", x: bx0 + 1.75, z: bz0 + 4.6, rotationY: Math.PI / 2, room: "break" },
    { id: "break-table", kind: "coffeeTable", x: bx0 + 3.6, z: bz0 + 4.6, rotationY: 0, room: "break" },
    { id: "coffee-bar", kind: "coffeeBar", x: bx0 + 7.9, z: bz0 + 0.5, rotationY: 0, room: "break" },
    { id: "water-cooler", kind: "waterCooler", x: breakRoom.maxX - 0.45, z: bz0 + 2.6, rotationY: -Math.PI / 2, room: "break" },
    { id: "arcade", kind: "arcade", x: breakRoom.maxX - 0.6, z: bottomZ - 0.7, rotationY: -Math.PI / 2, room: "break" },
    { id: "beanbag-a", kind: "beanbag", x: bx0 + 7.2, z: bz0 + 5.4, rotationY: 0, room: "break" },
    { id: "beanbag-b", kind: "beanbag", x: bx0 + 8.4, z: bz0 + 6.3, rotationY: 0, room: "break" },
    { id: "break-shelf", kind: "bookshelf", x: bx0 + 2.2, z: bz0 + 0.3, rotationY: 0, room: "break" },
    { id: "break-plant", kind: "plant", x: bx0 + 0.6, z: bottomZ - 0.6, rotationY: 0, room: "break" },
  ];
  for (const dept of departments) {
    furniture.push({ id: `${dept.id}-plant-w`, kind: "plant", x: dept.minX + 0.45, z: dept.maxZ - 0.45, rotationY: 0, room: "floor" });
    furniture.push({ id: `${dept.id}-plant-e`, kind: "plant", x: dept.maxX - 0.45, z: dept.maxZ - 0.45, rotationY: 0, room: "floor" });
  }

  const table = furniture.find((f) => f.id === "team-table")!;
  const spots: Spot[] = [
    // Couch seats: couch A faces north (rotated π), couch B faces east.
    { id: "couch-a1", kind: "couch", pose: "sit", x: bx0 + 3.0, z: bz0 + 6.15, facing: Math.PI, room: "break" },
    { id: "couch-a2", kind: "couch", pose: "sit", x: bx0 + 4.2, z: bz0 + 6.15, facing: Math.PI, room: "break" },
    { id: "couch-b1", kind: "couch", pose: "sit", x: bx0 + 1.9, z: bz0 + 4.0, facing: Math.PI / 2, room: "break" },
    { id: "couch-b2", kind: "couch", pose: "sleep", x: bx0 + 1.9, z: bz0 + 5.2, facing: Math.PI / 2, room: "break" },
    { id: "coffee-1", kind: "coffee", pose: "stand", x: bx0 + 7.3, z: bz0 + 1.4, facing: Math.PI, room: "break" },
    { id: "coffee-2", kind: "coffee", pose: "stand", x: bx0 + 8.5, z: bz0 + 1.4, facing: Math.PI, room: "break" },
    { id: "cooler", kind: "cooler", pose: "stand", x: breakRoom.maxX - 1.2, z: bz0 + 2.6, facing: Math.PI / 2, room: "break" },
    { id: "arcade", kind: "arcade", pose: "stand", x: breakRoom.maxX - 1.45, z: bottomZ - 0.7, facing: Math.PI / 2, room: "break" },
    { id: "beanbag-a", kind: "beanbag", pose: "sit", x: bx0 + 7.2, z: bz0 + 5.4, facing: Math.PI * 1.25, room: "break" },
    { id: "beanbag-b", kind: "beanbag", pose: "sit", x: bx0 + 8.4, z: bz0 + 6.3, facing: Math.PI * 1.25, room: "break" },
    { id: "shelf-break", kind: "shelf", pose: "stand", x: bx0 + 2.2, z: bz0 + 1.1, facing: Math.PI, room: "break" },
    { id: "shelf-lead", kind: "shelf", pose: "stand", x: leadRoom.maxX - 1.3, z: topZ + 1.2, facing: Math.PI, room: "lead" },
    { id: "board", kind: "board", pose: "stand", x: tcx - 0.6, z: topZ + 1.2, facing: Math.PI, room: "team" },
  ];
  // Meeting chairs: three per long side of the team table.
  for (let i = 0; i < 3; i += 1) {
    const x = table.x - 1.1 + i * 1.1;
    spots.push({ id: `meeting-n${i}`, kind: "meeting", pose: "sit", x, z: table.z - 1.25, facing: 0, room: "team" });
    spots.push({ id: `meeting-s${i}`, kind: "meeting", pose: "sit", x, z: table.z + 1.25, facing: Math.PI, room: "team" });
  }
  // Window spots along the east and west railing, looking out at the stars.
  for (const dept of departments) {
    const side = dept.minX <= minX + 0.01 ? "west" : dept.maxX >= maxX - 0.01 ? "east" : null;
    if (!side) continue;
    const zc = (dept.minZ + dept.maxZ) / 2;
    spots.push({
      id: `window-${dept.id}`, kind: "window", pose: "stand", room: "floor",
      x: side === "west" ? minX - EDGE + 0.9 : maxX + EDGE - 0.9, z: zc, facing: side === "west" ? -Math.PI / 2 : Math.PI / 2,
    });
  }

  const reception = furniture.find((f) => f.id === "reception-desk")!;
  const kiosk = furniture.find((f) => f.id === "kiosk")!;
  const checkpoints: Checkpoint[] = [
    { id: "create", room: "reception", x: reception.x, z: reception.z + 1.7, radius: 1.2 },
    { id: "manage", room: "reception", x: kiosk.x, z: kiosk.z + 1.4, radius: 1.1 },
    { id: "team", room: "team", x: tcx + 2.2, z: northMaxZ - 1.05, radius: 0.95 },
    { id: "wardrobe", room: "wardrobe", x: wcx, z: tcz + 0.8, radius: 1.5 },
    { id: "lead", room: "lead", x: minX + leadW / 2, z: northMaxZ - 1.5, radius: 1.3 },
    { id: "break", room: "break", x: bx0 + 5.6, z: bz0 + 2.6, radius: 1.8 },
  ];

  const walls = rooms.flatMap(wallsOf);
  const desks = [...lead.desks, ...departments.flatMap((d) => d.desks)];
  const obstacles: Rect[] = [
    ...walls.map(wallRect),
    ...desks.map((d) => ({ minX: d.x - DESK_SIZE.w / 2, maxX: d.x + DESK_SIZE.w / 2, minZ: d.z - DESK_SIZE.d / 2, maxZ: d.z + DESK_SIZE.d / 2 })),
    // Department sign walls along each department's north edge.
    ...departments.map((d) => ({ minX: d.minX + 0.2, maxX: d.maxX - 0.2, minZ: d.minZ + 0.02, maxZ: d.minZ + 0.2 })),
    ...furniture.filter((f) => FURNITURE_SIZE[f.kind].solid).map(footprint),
    // The posts of an open room's name arch are solid too; nobody walks through them.
    ...rooms.filter((r) => !r.walled).flatMap(archPosts).map((p) => ({
      minX: p.x - ARCH.post / 2, maxX: p.x + ARCH.post / 2, minZ: p.z - ARCH.post / 2, maxZ: p.z + ARCH.post / 2,
    })),
  ];

  const floor = { minX: minX - EDGE + 0.35, maxX: maxX + EDGE - 0.35, minZ: topZ - EDGE + 0.35, maxZ: bottomZ + EDGE - 0.35 };
  return {
    departments, lead, rooms, walls, furniture, spots, checkpoints, obstacles,
    spawn: { x: rx0 + 1.4, z: bottomZ - 2.6 },
    floor,
    bounds: { minX: minX - EDGE, maxX: maxX + EDGE, minZ: topZ - EDGE, maxZ: bottomZ + EDGE },
  };
}

/** Every desk on the floor, lead office first. */
export function allDesks(layout: OfficeLayout): DeskSlot[] {
  return [...layout.lead.desks, ...layout.departments.flatMap((d) => d.desks)];
}

export function roomAt(layout: OfficeLayout, p: Point): Room | null {
  return layout.rooms.find((r) => p.x >= r.minX && p.x <= r.maxX && p.z >= r.minZ && p.z <= r.maxZ) ?? null;
}

export interface StatusCounts { working: number; idle: number; waiting: number; paused: number }

export function countStates(agents: readonly Pick<OfficeAgentInput, "state">[]): StatusCounts {
  const counts: StatusCounts = { working: 0, idle: 0, waiting: 0, paused: 0 };
  for (const agent of agents) counts[agent.state] += 1;
  return counts;
}
