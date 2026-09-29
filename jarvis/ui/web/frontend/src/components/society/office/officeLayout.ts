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
 *
 * The coding floor (variant "coding", one elevator ride up) keeps the same
 * frame: its departments are the Agentic IDE workspaces, and the north strip
 * holds a quiet focus zone · the team room · a server room instead of the lead
 * office and the wardrobe. It has no lead desks and only the elevator and
 * break-room checkpoints — nothing there creates or dresses society agents.
 * It has exactly the agents office's footprint; its centre aisle holds
 * Mission Control: a screen on a floor stand between the two department
 * columns, where new coding agents are started and several can be briefed at once.
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
  /** The lead office's executive desk is larger than a bench desk; absent means DESK_SIZE. */
  size?: { w: number; d: number };
}

export interface Department extends Rect {
  id: string;
  /** Provider family shown on the sign; "" for a spare open-space department. */
  label: string;
  desks: DeskSlot[];
  /** Carpet tint index into the palette's department colours. */
  tint: number;
}

/** "focus" and "server" only exist on the coding floor, in place of the lead office and the wardrobe. */
export type RoomKind = "lead" | "team" | "wardrobe" | "reception" | "break" | "focus" | "server";

/** Which floor a layout draws: the society agents' office, or the coding agents' floor above it. */
export type OfficeVariant = "agents" | "coding";

export interface Door { side: "north" | "south" | "east" | "west"; /** Centre of the gap along the wall. */ at: number; width: number }

export interface Room extends Rect {
  id: RoomKind;
  kind: RoomKind;
  /** Walled rooms get glass walls on every side except their doors; open rooms have none. */
  walled: boolean;
  doors: Door[];
}

export interface WallSegment { x1: number; z1: number; x2: number; z2: number; room: RoomKind }

/**
 * "elevator" rides between the floors and stands in front of the lobby elevator on both of them.
 * "mission" is Mission Control, the coding floor's centre hub.
 */
export type CheckpointKind = "create" | "manage" | "team" | "wardrobe" | "lead" | "break" | "elevator" | "mission";

/** A place the person can walk to (or click from afar) to act. "floor" = the open office, outside every room. */
export interface Checkpoint extends Point {
  id: CheckpointKind; room: RoomKind | "floor";
  /** Walk-in radius in metres. */
  radius: number;
  /** Height of the floating token; absent = the standard height. Raised over tall props such as Mission Control's screen. */
  tokenY?: number;
  /**
   * Where "walk there" goes when the checkpoint's centre is inside something
   * solid (Mission Control's ring is centred on its screen); absent = the centre.
   */
  approach?: Point;
}

export type FurnitureKind =
  | "meetingTable" | "teamBoard" | "receptionDesk" | "kiosk" | "lockers" | "mirror"
  | "coffeeBar" | "waterCooler" | "couch" | "coffeeTable" | "arcade" | "beanbag"
  | "bookshelf" | "plant" | "rug" | "elevator"
  // Lead office: the executive suite.
  | "leadWall" | "executiveRug" | "guestChair" | "chesterfield" | "loungeTable" | "executiveBar" | "globe" | "floorLamp"
  | "dogBed" | "treatJar"
  // Coding floor: Mission Control's console ring.
  | "missionConsole";

/**
 * Footprint (x-extent × z-extent before rotation) and height of each piece.
 * The renderer MUST build every prop inside this box: navigation reads the
 * same numbers, so a prop larger than its footprint makes figures clip into it.
 */
export const FURNITURE_SIZE: Record<FurnitureKind, { w: number; d: number; h: number; solid: boolean }> = {
  meetingTable: { w: 3.6, d: 1.6, h: 0.76, solid: true },
  teamBoard: { w: 2.6, d: 0.2, h: 1.9, solid: true },
  // The counter is 1.1 m; its slatted back wall with the help display rises to 2.2 m.
  receptionDesk: { w: 2.8, d: 0.9, h: 2.2, solid: true },
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
  leadWall: { w: 7.9, d: 0.38, h: 2.9, solid: true },
  executiveRug: { w: 1, d: 1, h: 0.02, solid: false },
  guestChair: { w: 0.74, d: 0.74, h: 0.92, solid: true },
  chesterfield: { w: 2.2, d: 0.95, h: 0.86, solid: true },
  loungeTable: { w: 1.1, d: 0.64, h: 0.62, solid: true },
  executiveBar: { w: 1.9, d: 0.52, h: 1.9, solid: true },
  // The tilted meridian ring reaches further east-west than the stand.
  globe: { w: 0.92, d: 0.72, h: 1.2, solid: true },
  floorLamp: { w: 0.46, d: 0.46, h: 1.8, solid: true },
  dogBed: { w: 0.9, d: 0.72, h: 0.45, solid: true },
  treatJar: { w: 0.5, d: 0.5, h: 1.2, solid: true },
  // Mission Control: a display on a floor stand; the box is the screen's width and the base plate's depth.
  missionConsole: { w: 1.4, d: 0.5, h: 1.8, solid: true },
};

export interface Furniture extends Point {
  id: string;
  kind: FurnitureKind;
  /** Rotation about +y. 0 = the prop's front faces +z (south, towards the camera). */
  rotationY: number;
  room: RoomKind | "floor";
  /** Only rugs (plain and executive) are sized per instance (w × d); everything else uses FURNITURE_SIZE. */
  size?: { w: number; d: number };
}

export type SpotKind = "couch" | "coffee" | "cooler" | "window" | "shelf" | "arcade" | "meeting" | "beanbag" | "board";
export type SpotPose = "sit" | "stand" | "sleep";

/** Where an idle agent (or the person) can hang out. `facing` is the figure's rotation about +y. */
export interface Spot extends Point { id: string; kind: SpotKind; pose: SpotPose; facing: number; room: RoomKind | "floor" }

export interface OfficeLayout {
  variant: OfficeVariant;
  departments: Department[];
  /** Lead desks live in the lead office; the coding floor has none (its rect is the focus zone). */
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
/** The lead's executive desk: wider and a little deeper, same seat distance and height. */
export const EXECUTIVE_DESK_SIZE = { w: 2.4, d: 0.9 } as const;
/** A second lead's partner desk beside the executive desk. */
export const PARTNER_DESK_SIZE = { w: 1.7, d: 0.84 } as const;
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
/** How far round Mission Control's screen the person can use it, from its centre (metres). */
export const MISSION_REACH = 1.5;
const EDGE = 1.6;
/**
 * How far a figure's centre stays from the slab edge. The railing stands 0.2 m
 * in and the toy head is ~0.31 m wide each side (plus hair), so anything less
 * lets the head poke through the glass and the handrail cap.
 */
const RAIL_CLEARANCE = 0.65;
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

/** The footprint of a desk top (bench desks share DESK_SIZE; lead desks carry their own). */
export function deskRect(desk: Pick<DeskSlot, "x" | "z" | "size">): Rect {
  const { w, d } = desk.size ?? DESK_SIZE;
  return { minX: desk.x - w / 2, maxX: desk.x + w / 2, minZ: desk.z - d / 2, maxZ: desk.z + d / 2 };
}

/** Half the width of the executive chair; unlike bench chairs it is solid, so nobody walks through it. */
export const EXECUTIVE_CHAIR_HALF = 0.32;

/** The executive chair's footprint behind a lead desk (only desks with their own size have one). */
export function executiveChairRect(desk: Pick<DeskSlot, "x" | "z" | "facing">): Rect {
  const seat = seatOf(desk);
  return { minX: seat.x - EXECUTIVE_CHAIR_HALF, maxX: seat.x + EXECUTIVE_CHAIR_HALF, minZ: seat.z - EXECUTIVE_CHAIR_HALF, maxZ: seat.z + EXECUTIVE_CHAIR_HALF };
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

export interface OfficeLayoutOptions {
  /** "agents" (default) is the society office; "coding" the IDE sessions' floor, departments per workspace. */
  variant?: OfficeVariant;
}

/** Build the floor for a roster. Seating and the whole floor plan are stable for an unchanged roster. */
export function buildOfficeLayout(agents: readonly OfficeAgentInput[], options: OfficeLayoutOptions = {}): OfficeLayout {
  const variant = options.variant ?? "agents";
  const coding = variant === "coding";
  const groups: { label: string; members: OfficeAgentInput[] }[] = groupDepartments(agents);
  if (groups.length === 1 && groups[0].members.length === 0) groups.length = 0;
  while (groups.length < MIN_DEPARTMENTS) groups.push({ label: "", members: [] });

  // Both floors share one footprint: the elevator ride never changes the size of the world.
  // Mission Control's screen (1.4 m) fits the normal aisle with a walkway on each side.
  const centreAisle = AISLE;
  const floorWidth = COLUMNS * DEPT_WIDTH + (COLUMNS - 1) * centreAisle;
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
  // The coding floor puts a focus zone and a server room on the same footprints.
  const leadW = 8.2, teamW = 8.4;
  const northMaxZ = topZ + NORTH_DEPTH;
  const westKind: RoomKind = coding ? "focus" : "lead";
  const eastKind: RoomKind = coding ? "server" : "wardrobe";
  const leadRoom: Room = { id: westKind, kind: westKind, walled: true, minX, maxX: minX + leadW, minZ: topZ, maxZ: northMaxZ,
    doors: [{ side: "south", at: minX + leadW / 2, width: 1.6 }] };
  const teamRoom: Room = { id: "team", kind: "team", walled: true, minX: leadRoom.maxX, maxX: leadRoom.maxX + teamW, minZ: topZ, maxZ: northMaxZ,
    doors: [{ side: "south", at: leadRoom.maxX + teamW / 2, width: 1.8 }] };
  const wardrobeRoom: Room = { id: eastKind, kind: eastKind, walled: true, minX: teamRoom.maxX, maxX, minZ: topZ, maxZ: northMaxZ,
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
      const dMinX = minX + col * (DEPT_WIDTH + centreAisle);
      const id = `dept-${index}`;
      departments.push({
        id, label: group.label, tint: index,
        minX: dMinX, maxX: dMinX + DEPT_WIDTH, minZ: z, maxZ: z + deptDepth(benches),
        desks: layoutDesks(id, dMinX, z, benches, group.members.slice(0, benches * SEATS_PER_BENCH)),
      });
    }
    z += rowDepths[row] + AISLE;
  }
  // Mission Control sits in the middle of the department block, on the centre aisle.
  const departmentsMinZ = northMaxZ + AISLE;
  const missionZ = (departmentsMinZ + (z - AISLE)) / 2;

  // South strip: an open reception/lobby in the west, a walled break room in the east.
  const southMinZ = bottomZ - SOUTH_DEPTH;
  const breakW = 10.5;
  const receptionRoom: Room = { id: "reception", kind: "reception", walled: false, minX, maxX: maxX - breakW, minZ: southMinZ, maxZ: bottomZ, doors: [] };
  const breakRoom: Room = { id: "break", kind: "break", walled: true, minX: maxX - breakW, maxX, minZ: southMinZ, maxZ: bottomZ,
    doors: [{ side: "north", at: maxX - breakW / 2, width: 2.2 }, { side: "west", at: southMinZ + SOUTH_DEPTH / 2, width: 2 }] };
  const rooms = [leadRoom, teamRoom, wardrobeRoom, receptionRoom, breakRoom];

  // The lead's executive desk faces south, towards the door and the office. A
  // second lead gets a partner desk to its east; there is never an empty one.
  const leadAgents = coding ? [] : agents.filter((a) => a.tier === "lead").sort(byArrival).slice(0, 2);
  const lx0 = leadRoom.minX, lz0 = leadRoom.minZ;
  const leadDeskZ = lz0 + 2.6;
  const lead = {
    minX: leadRoom.minX, maxX: leadRoom.maxX, minZ: leadRoom.minZ, maxZ: leadRoom.maxZ,
    desks: coding ? [] : [
      { id: "lead:0", x: lx0 + leadW / 2, z: leadDeskZ, facing: "south", agentId: leadAgents[0]?.agentId ?? null, size: EXECUTIVE_DESK_SIZE },
      ...(leadAgents[1] ? [{ id: "lead:1", x: lx0 + 6.75, z: leadDeskZ, facing: "south" as const, agentId: leadAgents[1].agentId, size: PARTNER_DESK_SIZE }] : []),
    ] satisfies DeskSlot[],
  };

  const tcx = (teamRoom.minX + teamRoom.maxX) / 2, tcz = (teamRoom.minZ + teamRoom.maxZ) / 2;
  const wcx = (wardrobeRoom.minX + wardrobeRoom.maxX) / 2;
  const bx0 = breakRoom.minX, bz0 = breakRoom.minZ;
  const rx0 = receptionRoom.minX;
  const leadFurniture: Furniture[] = coding ? [] : [
    // Lead office: a panelled feature wall with lit bookcases and the star emblem,
    // a rug under the executive desk, guest armchairs, the bar and a leather
    // lounge along the west wall (their fronts face the camera), a globe and a
    // lamp in the east, palms beside the door.
    { id: "lead-wall", kind: "leadWall", x: lx0 + leadW / 2, z: lz0 + 0.1 + FURNITURE_SIZE.leadWall.d / 2, rotationY: 0, room: "lead" },
    { id: "lead-rug", kind: "executiveRug", x: lx0 + leadW / 2, z: lz0 + 3.55, rotationY: 0, room: "lead", size: { w: 4.6, d: 3.9 } },
    { id: "lead-guest-w", kind: "guestChair", x: lx0 + leadW / 2 - 0.72, z: leadDeskZ + 1.2, rotationY: Math.PI, room: "lead" },
    { id: "lead-guest-e", kind: "guestChair", x: lx0 + leadW / 2 + 0.72, z: leadDeskZ + 1.2, rotationY: Math.PI, room: "lead" },
    { id: "lead-sofa", kind: "chesterfield", x: lx0 + 0.6, z: lz0 + 4.55, rotationY: Math.PI / 2, room: "lead" },
    { id: "lead-table", kind: "loungeTable", x: lx0 + 1.62, z: lz0 + 4.55, rotationY: Math.PI / 2, room: "lead" },
    { id: "lead-lamp", kind: "floorLamp", x: lx0 + 0.4, z: lz0 + 3.1, rotationY: 0, room: "lead" },
    { id: "lead-bar", kind: "executiveBar", x: lx0 + 0.36, z: lz0 + 1.85, rotationY: Math.PI / 2, room: "lead" },
    { id: "lead-globe", kind: "globe", x: leadRoom.maxX - 0.75, z: lz0 + 4.3, rotationY: 0, room: "lead" },
    { id: "lead-lamp-e", kind: "floorLamp", x: leadRoom.maxX - 0.34, z: lz0 + 2.9, rotationY: 0, room: "lead" },
    { id: "lead-plant", kind: "plant", x: lx0 + 0.55, z: northMaxZ - 0.55, rotationY: 0, room: "lead" },
    { id: "lead-plant-e", kind: "plant", x: leadRoom.maxX - 0.55, z: northMaxZ - 0.55, rotationY: 0, room: "lead" },
    // The office dog's baskets: the lead office's north-east corner beside the
    // bookcase, the team room's north-west corner and the break room's corner
    // by the bookshelf. It walks between them now and then.
    { id: "lead-dog", kind: "dogBed", x: leadRoom.maxX - 0.58, z: lz0 + 0.98, rotationY: 0, room: "lead" },
    { id: "team-dog", kind: "dogBed", x: teamRoom.minX + 0.62, z: topZ + 0.95, rotationY: 0, room: "team" },
    { id: "break-dog", kind: "dogBed", x: breakRoom.minX + 0.62, z: breakRoom.minZ + 1.6, rotationY: 0, room: "break" },
    // Easter egg: the jar of dog treats next to the coffee bar.
    { id: "break-treats", kind: "treatJar", x: breakRoom.minX + 9.95, z: breakRoom.minZ + 0.5, rotationY: 0, room: "break" },
  ];
  // Coding floor, west: a quiet focus zone — a wall of shelves, a couch facing
  // them on a rug, a beanbag in each corner, palms beside the door.
  const focusFurniture: Furniture[] = coding ? [
    { id: "focus-shelf-w", kind: "bookshelf", x: lx0 + 1.6, z: lz0 + 0.35, rotationY: 0, room: "focus" },
    { id: "focus-shelf-c", kind: "bookshelf", x: lx0 + leadW / 2, z: lz0 + 0.35, rotationY: 0, room: "focus" },
    { id: "focus-shelf-e", kind: "bookshelf", x: lx0 + leadW - 1.6, z: lz0 + 0.35, rotationY: 0, room: "focus" },
    { id: "focus-rug", kind: "rug", x: lx0 + leadW / 2, z: lz0 + 3.6, rotationY: 0, room: "focus", size: { w: 4.8, d: 2.6 } },
    { id: "focus-couch", kind: "couch", x: lx0 + leadW / 2, z: lz0 + 4.4, rotationY: Math.PI, room: "focus" },
    { id: "focus-beanbag-w", kind: "beanbag", x: lx0 + 1.3, z: lz0 + 3.0, rotationY: 0, room: "focus" },
    { id: "focus-beanbag-e", kind: "beanbag", x: lx0 + leadW - 1.3, z: lz0 + 3.0, rotationY: 0, room: "focus" },
    { id: "focus-plant", kind: "plant", x: lx0 + 0.55, z: northMaxZ - 0.55, rotationY: 0, room: "focus" },
    { id: "focus-plant-e", kind: "plant", x: leadRoom.maxX - 0.55, z: northMaxZ - 0.55, rotationY: 0, room: "focus" },
  ] : [];
  const wx0 = wardrobeRoom.minX;
  const furniture: Furniture[] = [
    ...leadFurniture,
    ...focusFurniture,
    // Team room: one long table, a board on the north wall.
    { id: "team-table", kind: "meetingTable", x: tcx, z: tcz + 0.4, rotationY: 0, room: "team" },
    { id: "team-board", kind: "teamBoard", x: tcx, z: topZ + 0.25, rotationY: 0, room: "team" },
    { id: "team-plant", kind: "plant", x: teamRoom.maxX - 0.6, z: northMaxZ - 0.6, rotationY: 0, room: "team" },
    ...(coding ? [
      // Coding floor, east: a server room — a rack cabinet on the north wall and
      // console terminals along both side walls.
      { id: "server-rack", kind: "lockers", x: wcx, z: topZ + 0.4, rotationY: 0, room: "server" },
      { id: "server-console-e1", kind: "kiosk", x: wardrobeRoom.maxX - 0.35, z: topZ + 2.2, rotationY: -Math.PI / 2, room: "server" },
      { id: "server-console-e2", kind: "kiosk", x: wardrobeRoom.maxX - 0.35, z: topZ + 3.7, rotationY: -Math.PI / 2, room: "server" },
      { id: "server-console-w", kind: "kiosk", x: wx0 + 0.35, z: topZ + 2.2, rotationY: Math.PI / 2, room: "server" },
      { id: "server-plant", kind: "plant", x: wx0 + 0.55, z: northMaxZ - 0.55, rotationY: 0, room: "server" },
    ] satisfies Furniture[] : [
      // Wardrobe: lockers and a mirror.
      { id: "wardrobe-lockers", kind: "lockers", x: wcx - 0.2, z: topZ + 0.4, rotationY: 0, room: "wardrobe" },
      { id: "wardrobe-mirror", kind: "mirror", x: wardrobeRoom.maxX - 0.25, z: tcz, rotationY: -Math.PI / 2, room: "wardrobe" },
    ] satisfies Furniture[]),
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
    ...(coding ? [
      // Mission Control: a screen on a stand, midway down the centre aisle, facing the camera.
      { id: "mission-console", kind: "missionConsole", x: 0, z: missionZ, rotationY: 0, room: "floor" },
    ] satisfies Furniture[] : []),
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
    ...(coding ? [
      // Focus zone: browsing the shelf wall, the couch facing it, a beanbag per corner.
      { id: "shelf-focus-w", kind: "shelf", pose: "stand", x: lx0 + 1.6, z: lz0 + 1.15, facing: Math.PI, room: "focus" },
      { id: "shelf-focus-e", kind: "shelf", pose: "stand", x: lx0 + leadW - 1.6, z: lz0 + 1.15, facing: Math.PI, room: "focus" },
      { id: "couch-focus-1", kind: "couch", pose: "sit", x: lx0 + leadW / 2 - 0.6, z: lz0 + 4.25, facing: Math.PI, room: "focus" },
      { id: "couch-focus-2", kind: "couch", pose: "sit", x: lx0 + leadW / 2 + 0.6, z: lz0 + 4.25, facing: Math.PI, room: "focus" },
      { id: "beanbag-focus-w", kind: "beanbag", pose: "sit", x: lx0 + 1.3, z: lz0 + 3.0, facing: Math.PI * 0.75, room: "focus" },
      { id: "beanbag-focus-e", kind: "beanbag", pose: "sit", x: lx0 + leadW - 1.3, z: lz0 + 3.0, facing: Math.PI * 1.25, room: "focus" },
      // Server room: a look at the consoles on either wall.
      { id: "console-e", kind: "board", pose: "stand", x: wardrobeRoom.maxX - 1.2, z: topZ + 2.2, facing: Math.PI / 2, room: "server" },
      { id: "console-w", kind: "board", pose: "stand", x: wx0 + 1.2, z: topZ + 2.2, facing: -Math.PI / 2, room: "server" },
    ] satisfies Spot[] : [
      // In front of the feature wall's east bookcase.
      { id: "shelf-lead", kind: "shelf", pose: "stand", x: leadRoom.maxX - 1.45, z: topZ + 1.05, facing: Math.PI, room: "lead" },
    ] satisfies Spot[]),
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
  const elevator = furniture.find((f) => f.id === "elevator")!;
  // The elevator's doors face east into the lobby; its checkpoint is the floor in front of them.
  const elevatorStop: Checkpoint = { id: "elevator", room: "reception", x: elevator.x + 0.95, z: elevator.z, radius: 1.0 };
  const breakStop: Checkpoint = { id: "break", room: "break", x: bx0 + 5.6, z: bz0 + 2.6, radius: 1.8 };
  // Mission Control's stop is a ring round the screen, usable from every side; "walk there" ends in front of it.
  const missionStop: Checkpoint = { id: "mission", room: "floor", x: 0, z: missionZ, radius: MISSION_REACH, tokenY: 2.95,
    approach: { x: 0, z: missionZ + 0.9 } };
  const checkpoints: Checkpoint[] = coding ? [missionStop, elevatorStop, breakStop] : [
    { id: "create", room: "reception", x: reception.x, z: reception.z + 1.7, radius: 1.2 },
    { id: "manage", room: "reception", x: kiosk.x, z: kiosk.z + 1.4, radius: 1.1 },
    { id: "team", room: "team", x: tcx + 2.2, z: northMaxZ - 1.05, radius: 0.95 },
    { id: "wardrobe", room: "wardrobe", x: wcx, z: tcz + 0.8, radius: 1.5 },
    { id: "lead", room: "lead", x: minX + leadW / 2, z: northMaxZ - 1.5, radius: 1.3 },
    breakStop,
    elevatorStop,
  ];

  const walls = rooms.flatMap(wallsOf);
  const desks = [...lead.desks, ...departments.flatMap((d) => d.desks)];
  const obstacles: Rect[] = [
    ...walls.map(wallRect),
    ...desks.map(deskRect),
    ...lead.desks.filter((d) => d.size).map(executiveChairRect),
    // Department sign walls along each department's north edge.
    ...departments.map((d) => ({ minX: d.minX + 0.2, maxX: d.maxX - 0.2, minZ: d.minZ + 0.02, maxZ: d.minZ + 0.2 })),
    ...furniture.filter((f) => FURNITURE_SIZE[f.kind].solid).map(footprint),
    // The posts of an open room's name arch are solid too; nobody walks through them.
    ...rooms.filter((r) => !r.walled).flatMap(archPosts).map((p) => ({
      minX: p.x - ARCH.post / 2, maxX: p.x + ARCH.post / 2, minZ: p.z - ARCH.post / 2, maxZ: p.z + ARCH.post / 2,
    })),
  ];

  const floor = {
    minX: minX - EDGE + RAIL_CLEARANCE, maxX: maxX + EDGE - RAIL_CLEARANCE,
    minZ: topZ - EDGE + RAIL_CLEARANCE, maxZ: bottomZ + EDGE - RAIL_CLEARANCE,
  };
  return {
    variant, departments, lead, rooms, walls, furniture, spots, checkpoints, obstacles,
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
