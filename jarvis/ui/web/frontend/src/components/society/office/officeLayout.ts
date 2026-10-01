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
 *   │        departments (open office, 2 columns) · spawn point in the middle  │
 *   └──── south strip: Reception + lobby (Agent board) · Break room ─────────┘
 *
 * The coding floor (variant "coding", one elevator ride up) keeps the same
 * frame: its departments are the Agentic IDE workspaces, and the north strip
 * holds Mission Control · the team room · a server room instead of the lead
 * office and the wardrobe. It has no lead desks. Mission Control is a modern
 * office: a walnut slat wall with a live video wall, a desk with three live
 * monitors and a lounge corner; working it starts new coding agents and
 * briefs several at once. Only it, the spawn point, the elevator and the
 * break room are checkpoints there.
 *
 * Both floors have a spawn point in their middle: a round pad with a
 * terminal on the crossing of the centre aisle and the cross aisle nearest
 * the floor's centre. It spawns new Jarvis agents downstairs and new coding
 * agents upstairs, and everyone new to the floor appears on its pad.
 */
import type { AgentRunState, AgentTier } from "../data";
import { agentsAmbience } from "./agentsAmbience";
import { codingAmbience } from "./codingAmbience";

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

/** "command" (Mission Control) and "server" only exist on the coding floor, in place of the lead office and the wardrobe. */
export type RoomKind = "lead" | "team" | "wardrobe" | "reception" | "break" | "command" | "server";

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
 * "mission" is Mission Control on the coding floor.
 * "spawn" (agents floor) and "launch" (coding floor) are the spawn point in the floor's middle:
 * a new Jarvis agent downstairs, a new coding agent upstairs.
 */
export type CheckpointKind = "spawn" | "launch" | "create" | "manage" | "team" | "wardrobe" | "lead" | "break" | "elevator" | "mission";

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
  | "meetingTable" | "teamBoard" | "receptionDesk" | "lockers" | "mirror"
  | "coffeeBar" | "waterCooler" | "couch" | "coffeeTable" | "arcade" | "beanbag"
  | "bookshelf" | "plant" | "rug" | "elevator"
  // Break room lounge: sofa modules and their corner, the oak coffee table, a kilim, the bookcase wall,
  // the reading nook, foosball, monsteras and the arcade's game mat.
  | "breakSofa" | "breakSofaCorner" | "breakTable" | "breakRug" | "breakBookcase" | "readingNook" | "foosball" | "breakPlant"
  | "arcadeMat"
  // Lead office: the executive suite.
  | "leadWall" | "executiveRug" | "guestChair" | "chesterfield" | "loungeTable" | "executiveBar" | "globe" | "floorLamp"
  | "dogBed" | "treatJar"
  // Coding floor: Mission Control's console ring.
  // Coding floor: Mission Control's slat wall with the video wall, and its desk with three monitors and a chair.
  | "commandWall" | "commandDesk"
  // Coding floor: the server room — rack rows round a cold aisle, the NOC console and status wall, UPS, fire suppression.
  | "serverRack" | "coldAisle" | "nocConsole" | "statusWall" | "ups" | "fireSuppression"
  // Team room: the slat wall behind the board, a credenza, fiddle-leaf figs and a wool rug.
  | "teamWall" | "credenza" | "designerPlant" | "teamRug"
  // Wardrobe (agents floor): the oak wardrobe wall, a bulb-lit mirror, a tailor's dummy, a bench, a coat stand, a round rug.
  | "wardrobeWall" | "dressingMirror" | "tailorDummy" | "dressingBench" | "coatStand" | "roundRug"
  // Lobby (agents floor): the brand wall, the Agent board totem, the waiting lounge, olive trees, the award vitrine, the mat.
  | "brandWall" | "agentTotem" | "lobbySofa" | "lobbyArmchair" | "lobbyTable" | "sideTable" | "lobbyLamp" | "oliveTree"
  | "awardCase" | "entranceMat" | "lobbyRug"
  // The spawn point in the middle of both floors: the flat pad and the terminal standing on it.
  | "spawnPad" | "spawnTerminal";

/**
 * Footprint (x-extent × z-extent before rotation) and height of each piece.
 * The renderer MUST build every prop inside this box: navigation reads the
 * same numbers, so a prop larger than its footprint makes figures clip into it.
 */
export const FURNITURE_SIZE: Record<FurnitureKind, { w: number; d: number; h: number; solid: boolean }> = {
  // The top is at 0.76 m; a carafe stands on it.
  meetingTable: { w: 3.6, d: 1.6, h: 1.0, solid: true },
  teamBoard: { w: 2.6, d: 0.2, h: 1.9, solid: true },
  // The counter is 1.1 m; its slatted back wall with the help display rises to 2.2 m.
  receptionDesk: { w: 2.8, d: 0.9, h: 2.2, solid: true },
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
  // Break room: the sofa modules keep the couch's box; the coffee table carries a chess game and board games;
  // the reading nook is the armchair plus its floor lamp; the foosball box includes the rod handles.
  breakSofa: { w: 2.2, d: 0.9, h: 0.85, solid: true },
  breakSofaCorner: { w: 0.9, d: 0.9, h: 0.85, solid: true },
  breakTable: { w: 1.3, d: 0.75, h: 0.5, solid: true },
  breakRug: { w: 1, d: 1, h: 0.02, solid: false },
  breakBookcase: { w: 2.8, d: 0.4, h: 2.05, solid: true },
  readingNook: { w: 0.9, d: 0.9, h: 1.75, solid: true },
  foosball: { w: 1.3, d: 1.05, h: 0.8, solid: true },
  breakPlant: { w: 0.7, d: 0.7, h: 1.9, solid: true },
  arcadeMat: { w: 1, d: 1, h: 0.02, solid: false },
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
  commandWall: { w: 7.8, d: 0.3, h: 2.7, solid: true },
  // Desk and chair together; not a solid box, navigation walks round commandDeskObstacles.
  commandDesk: { w: 2.8, d: 2.1, h: 1.7, solid: false },
  // Server room: a row of five 42U racks, its ladder tray on top; the aisle between two rows (floor tiles,
  // overhead trays, a light) is walkable; the NOC console includes its stool.
  serverRack: { w: 3.0, d: 1.0, h: 2.3, solid: true },
  coldAisle: { w: 2.44, d: 3.0, h: 2.3, solid: false },
  nocConsole: { w: 2.0, d: 1.3, h: 1.3, solid: true },
  statusWall: { w: 2.4, d: 0.3, h: 2.1, solid: true },
  ups: { w: 1.2, d: 0.8, h: 1.9, solid: true },
  fireSuppression: { w: 0.9, d: 0.5, h: 1.9, solid: true },
  // Team room: the slat wall carries the board, felt panels and the video display.
  teamWall: { w: 8.16, d: 0.12, h: 2.05, solid: true },
  // The body is 0.61 m; books and a vase of branches stand on it.
  credenza: { w: 2.2, d: 0.46, h: 1.1, solid: true },
  designerPlant: { w: 0.8, d: 0.8, h: 2.0, solid: true },
  teamRug: { w: 1, d: 1, h: 0.02, solid: false },
  // Wardrobe: four open oak bays with rails, shelves and drawers; the mirror stands on splayed feet.
  wardrobeWall: { w: 4.36, d: 0.62, h: 2.05, solid: true },
  dressingMirror: { w: 1.1, d: 0.3, h: 2.05, solid: true },
  tailorDummy: { w: 0.5, d: 0.5, h: 1.75, solid: true },
  dressingBench: { w: 1.3, d: 0.5, h: 0.5, solid: true },
  coatStand: { w: 0.5, d: 0.5, h: 1.85, solid: true },
  roundRug: { w: 1, d: 1, h: 0.02, solid: false },
  // Lobby: the ghost and the wordmark on the brand wall; the totem is the Agent board ("manage").
  brandWall: { w: 3.2, d: 0.36, h: 2.9, solid: true },
  agentTotem: { w: 0.9, d: 0.5, h: 2.1, solid: true },
  lobbySofa: { w: 2.3, d: 0.95, h: 0.8, solid: true },
  lobbyArmchair: { w: 0.8, d: 0.82, h: 0.82, solid: true },
  // The top is at 0.4 m; magazines, a bowl and a sprig in a vase stand on it.
  lobbyTable: { w: 1.2, d: 0.75, h: 0.6, solid: true },
  sideTable: { w: 0.5, d: 0.5, h: 0.65, solid: true },
  lobbyLamp: { w: 0.5, d: 0.5, h: 1.75, solid: true },
  oliveTree: { w: 1.2, d: 1.2, h: 2.5, solid: true },
  awardCase: { w: 1.3, d: 0.42, h: 1.95, solid: true },
  entranceMat: { w: 1, d: 1, h: 0.02, solid: false },
  lobbyRug: { w: 1, d: 1, h: 0.02, solid: false },
  // The spawn point: a flat round pad everyone walks over, and the double-sided terminal on it (its crest
  // tops out at 2.2 m). The pad fits the 3.2 m aisle crossing with a margin to every department.
  spawnPad: { w: 2 * 1.42, d: 2 * 1.42, h: 0.02, solid: false },
  spawnTerminal: { w: 1.2, d: 0.7, h: 2.2, solid: true },
};

/**
 * The spawn point's numbers, shared by the layout, the renderer and the tests:
 * the walk-in radius round the terminal, how far south of it "walk there" and
 * a newcomer's arrival stand, and the height of its floating token (above
 * the terminal's crest).
 */
export const SPAWN = { reach: 1.15, approach: 0.95, tokenY: 2.85 } as const;

export interface Furniture extends Point {
  id: string;
  kind: FurnitureKind;
  /** Rotation about +y. 0 = the prop's front faces +z (south, towards the camera). */
  rotationY: number;
  room: RoomKind | "floor";
  /** Only rugs (plain, executive, team, lobby) and the entrance mat are sized per instance (w × d); everything else uses FURNITURE_SIZE. */
  size?: { w: number; d: number };
}

export type SpotKind = "couch" | "coffee" | "cooler" | "window" | "shelf" | "arcade" | "meeting" | "beanbag" | "board";
export type SpotPose = "sit" | "stand" | "sleep";

/** Where an idle agent (or the person) can hang out. `facing` is the figure's rotation about +y. */
export interface Spot extends Point { id: string; kind: SpotKind; pose: SpotPose; facing: number; room: RoomKind | "floor" }

export interface OfficeLayout {
  variant: OfficeVariant;
  departments: Department[];
  /** Lead desks live in the lead office; the coding floor has none (its rect is Mission Control). */
  lead: Rect & { desks: DeskSlot[] };
  /** Mission Control's desk on the coding floor, as a seat the person can take; absent downstairs. */
  command?: DeskSlot;
  rooms: Room[];
  walls: WallSegment[];
  furniture: Furniture[];
  spots: Spot[];
  checkpoints: Checkpoint[];
  /** Solid footprints (desks, walls, furniture), un-inflated. Navigation inflates them by the walker radius. */
  obstacles: Rect[];
  /** Where the person's character starts. */
  spawn: Point;
  /** Where someone new to the floor (an agent created during the visit) appears: on the spawn pad. */
  arrival: Point;
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
/**
 * Mission Control's desk, in its own space (origin = desk centre, +z = the
 * side where the chair stands): the desk top and the chair the person works
 * from. On the floor the desk is turned round, so the chair stands between
 * it and the wall. The renderer and navigation both read these numbers.
 */
/** `chairZ` equals SEAT_OFFSET, so the chair is exactly where seatOf() puts a seated person. */
export const COMMAND_DESK = { w: 2.6, d: 0.86, chairZ: 0.62 } as const;
/** How far round Mission Control's desk the person can use it, from the desk's centre. */
export const COMMAND_REACH = 1.9;
/** The desk piece's origin sits this far from the desk's centre, towards the chair: its box covers desk and chair. */
export const COMMAND_DESK_OFFSET = 0.5;
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

/** A planter box at each end of a bench (both floors): its width, length and the gap to the end desk. */
export const BENCH_PLANTER = { w: 0.36, d: 1.56, gap: 0.14 } as const;

/**
 * The planter boxes closing off each bench of a department, west and east,
 * keyed "<deptId>:<bench>:w|e". They stand in the dead end between the two
 * desk rows, clear of the chairs and the walkway behind them.
 */
export function benchPlanters(dept: Pick<Department, "id" | "desks">): { key: string; rect: Rect }[] {
  const benches = new Map<string, DeskSlot[]>();
  for (const desk of dept.desks) {
    const bench = desk.id.split(":").at(-3) ?? "0";
    benches.set(bench, [...(benches.get(bench) ?? []), desk]);
  }
  const half = DESK_SIZE.w / 2 + BENCH_PLANTER.gap;
  return [...benches.entries()].flatMap(([bench, desks]) => {
    const z = desks.reduce((sum, d) => sum + d.z, 0) / desks.length;
    const west = Math.min(...desks.map((d) => d.x)) - half, east = Math.max(...desks.map((d) => d.x)) + half;
    const zs = { minZ: z - BENCH_PLANTER.d / 2, maxZ: z + BENCH_PLANTER.d / 2 };
    return [
      { key: `${dept.id}:${bench}:w`, rect: { minX: west - BENCH_PLANTER.w, maxX: west, ...zs } },
      { key: `${dept.id}:${bench}:e`, rect: { minX: east, maxX: east + BENCH_PLANTER.w, ...zs } },
    ];
  });
}

/** Half the width of the executive chair; unlike bench chairs it is solid, so nobody walks through it. */
export const EXECUTIVE_CHAIR_HALF = 0.32;

/** The executive chair's footprint behind a lead desk (only desks with their own size have one). */
export function executiveChairRect(desk: Pick<DeskSlot, "x" | "z" | "facing">): Rect {
  const seat = seatOf(desk);
  return { minX: seat.x - EXECUTIVE_CHAIR_HALF, maxX: seat.x + EXECUTIVE_CHAIR_HALF, minZ: seat.z - EXECUTIVE_CHAIR_HALF, maxZ: seat.z + EXECUTIVE_CHAIR_HALF };
}

/** Half the width of a bench desk or meeting chair: its five-star base reaches ~0.29 m from the column. */
export const CHAIR_HALF = 0.28;

/** A chair's square footprint round its seat centre. Chairs are solid; walks to a seat snap onto it. */
export function chairRect(seat: Point, half = CHAIR_HALF): Rect {
  return { minX: seat.x - half, maxX: seat.x + half, minZ: seat.z - half, maxZ: seat.z + half };
}

/** What a walker bumps into at Mission Control's desk: the desk top and the chair, nothing in between. */
export function commandDeskObstacles(desk: Point): Rect[] {
  const { w, d } = COMMAND_DESK;
  return [
    { minX: desk.x - w / 2, maxX: desk.x + w / 2, minZ: desk.z - d / 2, maxZ: desk.z + d / 2 },
    // The desk is turned round on the floor, facing south: its chair stands north of it,
    // towards the wall (seatOf). A boss chair, so it takes the executive chair's footprint.
    executiveChairRect({ x: desk.x, z: desk.z, facing: "south" }),
  ];
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
  // The coding floor puts Mission Control and a server room on the same footprints.
  const leadW = 8.2, teamW = 8.4;
  const northMaxZ = topZ + NORTH_DEPTH;
  const westKind: RoomKind = coding ? "command" : "lead";
  const eastKind: RoomKind = coding ? "server" : "wardrobe";
  const leadRoom: Room = { id: westKind, kind: westKind, walled: true, minX, maxX: minX + leadW, minZ: topZ, maxZ: northMaxZ,
    doors: [{ side: "south", at: minX + leadW / 2, width: 1.6 }] };
  const teamRoom: Room = { id: "team", kind: "team", walled: true, minX: leadRoom.maxX, maxX: leadRoom.maxX + teamW, minZ: topZ, maxZ: northMaxZ,
    doors: [{ side: "south", at: leadRoom.maxX + teamW / 2, width: 1.8 }] };
  const wardrobeRoom: Room = { id: eastKind, kind: eastKind, walled: true, minX: teamRoom.maxX, maxX, minZ: topZ, maxZ: northMaxZ,
    doors: [{ side: "south", at: (teamRoom.maxX + maxX) / 2, width: 1.5 }] };

  // Departments between the strips, and the centre line of each cross aisle between two rows.
  const departments: Department[] = [];
  const crossAisles: number[] = [];
  let z = northMaxZ + AISLE;
  for (let row = 0; row < rows; row += 1) {
    if (row > 0) crossAisles.push(z - AISLE / 2);
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

  // South strip: an open reception/lobby in the west, a walled break room in the east.
  const southMinZ = bottomZ - SOUTH_DEPTH;
  // The spawn point: on the centre aisle, at the crossing nearest the floor's middle (a
  // single row of departments, which the minimum never allows, falls back to the south aisle).
  const plazaZ = (crossAisles.length > 0 ? crossAisles : [southMinZ - AISLE / 2])
    .reduce((best, c) => (Math.abs(c - (topZ + bottomZ) / 2) < Math.abs(best - (topZ + bottomZ) / 2) ? c : best));
  const plaza: Point = { x: minX + DEPT_WIDTH + centreAisle / 2, z: plazaZ };
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
  // Coding floor, west: Mission Control. The slat wall with the video wall
  // along the north side, the desk in front of it turned towards the door:
  // the person sits between desk and wall, facing into the room, and the
  // monitors face them. A lounge corner with a sofa facing east, an open
  // shelf on the east wall.
  const commandDeskAt = { x: lx0 + leadW / 2, z: lz0 + 2.75 };
  const commandFurniture: Furniture[] = coding ? [
    { id: "command-wall", kind: "commandWall", x: lx0 + leadW / 2, z: lz0 + 0.1 + FURNITURE_SIZE.commandWall.d / 2, rotationY: 0, room: "command" },
    // The piece's box is desk + chair; its origin sits between them.
    { id: "command-desk", kind: "commandDesk", x: commandDeskAt.x, z: commandDeskAt.z - COMMAND_DESK_OFFSET, rotationY: Math.PI, room: "command" },
    { id: "command-rug", kind: "rug", x: lx0 + 1.55, z: lz0 + 5.1, rotationY: 0, room: "command", size: { w: 2.7, d: 2.6 } },
    { id: "command-sofa", kind: "couch", x: lx0 + 0.6, z: lz0 + 5.1, rotationY: Math.PI / 2, room: "command" },
    { id: "command-table", kind: "coffeeTable", x: lx0 + 1.9, z: lz0 + 5.1, rotationY: 0, room: "command" },
    { id: "command-shelf", kind: "bookshelf", x: leadRoom.maxX - 0.3, z: lz0 + 4.7, rotationY: -Math.PI / 2, room: "command" },
    { id: "command-plant-w", kind: "plant", x: lx0 + 0.5, z: lz0 + 0.95, rotationY: 0, room: "command" },
    { id: "command-plant-e", kind: "plant", x: leadRoom.maxX - 0.5, z: lz0 + 0.95, rotationY: 0, room: "command" },
    { id: "command-plant-s", kind: "plant", x: leadRoom.maxX - 0.55, z: northMaxZ - 0.55, rotationY: 0, room: "command" },
  ] : [];
  const wx0 = wardrobeRoom.minX;
  // Server room: the rack rows' centre line and the cold aisle between their fronts.
  const serverRowZ = topZ + 3.5;
  const serverAisleMinX = wx0 + 0.06 + FURNITURE_SIZE.serverRack.d;
  const serverAisleMaxX = wardrobeRoom.maxX - 0.06 - FURNITURE_SIZE.serverRack.d;
  const furniture: Furniture[] = [
    ...leadFurniture,
    ...commandFurniture,
    // Team room: a walnut table on a wool rug, the board on a slat wall with
    // felt panels and a video display, a credenza under the display and one
    // along the west wall, fiddle-leaf figs in both corners by the door.
    { id: "team-rug", kind: "teamRug", x: tcx, z: tcz + 0.4, rotationY: 0, room: "team", size: { w: 5.4, d: 4.0 } },
    { id: "team-table", kind: "meetingTable", x: tcx, z: tcz + 0.4, rotationY: 0, room: "team" },
    { id: "team-wall", kind: "teamWall", x: tcx, z: topZ + 0.06 + FURNITURE_SIZE.teamWall.d / 2, rotationY: 0, room: "team" },
    { id: "team-board", kind: "teamBoard", x: tcx, z: topZ + 0.25, rotationY: 0, room: "team" },
    { id: "team-credenza", kind: "credenza", x: tcx + 2.7, z: topZ + 0.19 + FURNITURE_SIZE.credenza.d / 2, rotationY: 0, room: "team" },
    { id: "team-credenza-w", kind: "credenza", x: teamRoom.minX + 0.07 + FURNITURE_SIZE.credenza.d / 2, z: tcz + 0.4, rotationY: Math.PI / 2, room: "team" },
    { id: "team-plant", kind: "designerPlant", x: teamRoom.maxX - 0.6, z: northMaxZ - 0.6, rotationY: 0, room: "team" },
    { id: "team-plant-w", kind: "designerPlant", x: teamRoom.minX + 0.6, z: northMaxZ - 0.6, rotationY: Math.PI / 2, room: "team" },
    ...(coding ? [
      // Coding floor, east: a server room — two rack rows back to the side walls,
      // fronts facing across a cold aisle that runs from the door to the NOC
      // console and the status wall on the north side; the UPS and the
      // fire-suppression bank stand in the corners by the door.
      { id: "server-status", kind: "statusWall", x: wcx, z: topZ + 0.08 + FURNITURE_SIZE.statusWall.d / 2, rotationY: 0, room: "server" },
      { id: "server-noc", kind: "nocConsole", x: wcx, z: topZ + 0.5 + FURNITURE_SIZE.nocConsole.d / 2, rotationY: 0, room: "server" },
      { id: "server-rack-w", kind: "serverRack", x: wx0 + 0.06 + FURNITURE_SIZE.serverRack.d / 2, z: serverRowZ, rotationY: Math.PI / 2, room: "server" },
      { id: "server-rack-e", kind: "serverRack", x: wardrobeRoom.maxX - 0.06 - FURNITURE_SIZE.serverRack.d / 2, z: serverRowZ, rotationY: -Math.PI / 2, room: "server" },
      { id: "server-aisle", kind: "coldAisle", x: wcx, z: serverRowZ, rotationY: 0, room: "server" },
      { id: "server-ups", kind: "ups", x: wx0 + 0.06 + FURNITURE_SIZE.ups.d / 2, z: topZ + 6.0, rotationY: Math.PI / 2, room: "server" },
      { id: "server-fire", kind: "fireSuppression", x: wardrobeRoom.maxX - 0.06 - FURNITURE_SIZE.fireSuppression.d / 2, z: topZ + 6.0, rotationY: -Math.PI / 2, room: "server" },
    ] satisfies Furniture[] : [
      // Wardrobe: an open oak wardrobe along the north wall, a bulb-lit mirror
      // on the west side facing the room, a tailor's dummy, a bench on the east
      // side, a round rug under the checkpoint, a fig and a coat stand by the door.
      { id: "wardrobe-wall", kind: "wardrobeWall", x: wcx, z: topZ + 0.06 + FURNITURE_SIZE.wardrobeWall.d / 2, rotationY: 0, room: "wardrobe" },
      { id: "wardrobe-rug", kind: "roundRug", x: wcx, z: tcz + 0.3, rotationY: 0, room: "wardrobe", size: { w: 2.8, d: 2.8 } },
      { id: "wardrobe-mirror", kind: "dressingMirror", x: wx0 + 0.06 + FURNITURE_SIZE.dressingMirror.d / 2, z: tcz - 0.6, rotationY: Math.PI / 2, room: "wardrobe" },
      { id: "wardrobe-dummy", kind: "tailorDummy", x: wardrobeRoom.maxX - 0.6, z: topZ + 1.4, rotationY: -Math.PI / 4, room: "wardrobe" },
      { id: "wardrobe-bench", kind: "dressingBench", x: wardrobeRoom.maxX - 0.06 - FURNITURE_SIZE.dressingBench.d / 2, z: tcz + 0.1, rotationY: -Math.PI / 2, room: "wardrobe" },
      { id: "wardrobe-plant", kind: "designerPlant", x: wx0 + 0.5, z: northMaxZ - 0.5, rotationY: 0, room: "wardrobe" },
      { id: "wardrobe-stand", kind: "coatStand", x: wardrobeRoom.maxX - 0.4, z: northMaxZ - 0.45, rotationY: 0, room: "wardrobe" },
    ] satisfies Furniture[]),
    // Reception and lobby.
    { id: "elevator", kind: "elevator", x: rx0 + 0.25, z: bottomZ - 2.6, rotationY: Math.PI / 2, room: "reception" },
    { id: "reception-desk", kind: "receptionDesk", x: rx0 + 3.2, z: southMinZ + 2.6, rotationY: 0, room: "reception" },
    // The spawn point in the middle of the floor: the pad, and the terminal on it facing the lobby.
    { id: "spawn-pad", kind: "spawnPad", x: plaza.x, z: plaza.z, rotationY: 0, room: "floor" },
    { id: "spawn-terminal", kind: "spawnTerminal", x: plaza.x, z: plaza.z, rotationY: 0, room: "floor" },
    ...(coding ? [
      // The coding lobby keeps only its plants: new coding agents are spawned in the middle of the floor.
      { id: "lobby-plant-a", kind: "plant", x: rx0 + 0.6, z: southMinZ + 0.8, rotationY: 0, room: "reception" },
      { id: "lobby-plant-b", kind: "plant", x: receptionRoom.maxX - 0.8, z: bottomZ - 0.7, rotationY: 0, room: "reception" },
    ] satisfies Furniture[] : lobbyFurniture(receptionRoom)),
    // Break room lounge (both floors): an L-shaped sectional — module A along the
    // south facing north, module B along the west facing east, the corner module
    // joining them — round an oak coffee table on a kilim; the bookcase wall and
    // the coffee bar on the north wall; the reading nook in the north-west corner
    // by the dog's basket; foosball, beanbags, the water station and the arcade
    // on its pixel mat in the east; monsteras in two corners.
    { id: "break-rug", kind: "breakRug", x: bx0 + 3.4, z: bz0 + 4.9, rotationY: 0, room: "break", size: { w: 5.2, d: 4.2 } },
    { id: "break-sofa-a", kind: "breakSofa", x: bx0 + 3.3, z: bz0 + 6.3, rotationY: Math.PI, room: "break" },
    { id: "break-sofa-b", kind: "breakSofa", x: bx0 + 1.75, z: bz0 + 4.75, rotationY: Math.PI / 2, room: "break" },
    { id: "break-sofa-corner", kind: "breakSofaCorner", x: bx0 + 1.75, z: bz0 + 6.3, rotationY: Math.PI, room: "break" },
    { id: "break-table", kind: "breakTable", x: bx0 + 3.35, z: bz0 + 4.8, rotationY: 0, room: "break" },
    { id: "coffee-bar", kind: "coffeeBar", x: bx0 + 7.9, z: bz0 + 0.5, rotationY: 0, room: "break" },
    { id: "water-cooler", kind: "waterCooler", x: breakRoom.maxX - 0.45, z: bz0 + 2.6, rotationY: -Math.PI / 2, room: "break" },
    { id: "arcade", kind: "arcade", x: breakRoom.maxX - 0.6, z: bottomZ - 0.7, rotationY: -Math.PI / 2, room: "break" },
    { id: "arcade-mat", kind: "arcadeMat", x: breakRoom.maxX - 1.5, z: bottomZ - 0.7, rotationY: 0, room: "break", size: { w: 1.1, d: 1.2 } },
    { id: "foosball", kind: "foosball", x: bx0 + 8.1, z: bz0 + 4.1, rotationY: 0, room: "break" },
    { id: "beanbag-a", kind: "beanbag", x: bx0 + 6.6, z: bz0 + 6.1, rotationY: 0, room: "break" },
    { id: "beanbag-b", kind: "beanbag", x: bx0 + 7.8, z: bz0 + 6.75, rotationY: 0, room: "break" },
    { id: "break-shelf", kind: "breakBookcase", x: bx0 + 2.4, z: bz0 + 0.06 + FURNITURE_SIZE.breakBookcase.d / 2, rotationY: 0, room: "break" },
    { id: "break-nook", kind: "readingNook", x: bx0 + 0.53, z: bz0 + 0.58, rotationY: 0, room: "break" },
    { id: "break-plant", kind: "breakPlant", x: bx0 + 0.6, z: bottomZ - 0.6, rotationY: 0, room: "break" },
    { id: "break-plant-e", kind: "breakPlant", x: breakRoom.maxX - 0.45, z: bz0 + 5.9, rotationY: 0, room: "break" },
  ];
  for (const dept of departments) {
    furniture.push({ id: `${dept.id}-plant-w`, kind: "plant", x: dept.minX + 0.45, z: dept.maxZ - 0.45, rotationY: 0, room: "floor" });
    furniture.push({ id: `${dept.id}-plant-e`, kind: "plant", x: dept.maxX - 0.45, z: dept.maxZ - 0.45, rotationY: 0, room: "floor" });
  }

  const table = furniture.find((f) => f.id === "team-table")!;
  const spots: Spot[] = [
    // Couch seats: sofa module A faces north (rotated π), module B faces east;
    // the reading nook's armchair faces south, into the room.
    { id: "couch-a1", kind: "couch", pose: "sit", x: bx0 + 2.7, z: bz0 + 6.15, facing: Math.PI, room: "break" },
    { id: "couch-a2", kind: "couch", pose: "sit", x: bx0 + 3.7, z: bz0 + 6.15, facing: Math.PI, room: "break" },
    { id: "couch-b1", kind: "couch", pose: "sit", x: bx0 + 1.9, z: bz0 + 4.3, facing: Math.PI / 2, room: "break" },
    { id: "couch-b2", kind: "couch", pose: "sleep", x: bx0 + 1.9, z: bz0 + 5.35, facing: Math.PI / 2, room: "break" },
    { id: "couch-nook", kind: "couch", pose: "sit", x: bx0 + 0.61, z: bz0 + 0.72, facing: 0, room: "break" },
    { id: "coffee-1", kind: "coffee", pose: "stand", x: bx0 + 7.3, z: bz0 + 1.4, facing: Math.PI, room: "break" },
    { id: "coffee-2", kind: "coffee", pose: "stand", x: bx0 + 8.5, z: bz0 + 1.4, facing: Math.PI, room: "break" },
    { id: "cooler", kind: "cooler", pose: "stand", x: breakRoom.maxX - 1.2, z: bz0 + 2.6, facing: Math.PI / 2, room: "break" },
    { id: "arcade", kind: "arcade", pose: "stand", x: breakRoom.maxX - 1.45, z: bottomZ - 0.7, facing: Math.PI / 2, room: "break" },
    { id: "beanbag-a", kind: "beanbag", pose: "sit", x: bx0 + 6.6, z: bz0 + 6.1, facing: Math.PI * 1.25, room: "break" },
    { id: "beanbag-b", kind: "beanbag", pose: "sit", x: bx0 + 7.8, z: bz0 + 6.75, facing: Math.PI * 1.25, room: "break" },
    { id: "shelf-break", kind: "shelf", pose: "stand", x: bx0 + 2.2, z: bz0 + 1.1, facing: Math.PI, room: "break" },
    ...(coding ? [
      // Mission Control: the sofa facing east, the open shelf on the east wall.
      { id: "couch-command-1", kind: "couch", pose: "sit", x: lx0 + 0.75, z: lz0 + 4.55, facing: Math.PI / 2, room: "command" },
      { id: "couch-command-2", kind: "couch", pose: "sit", x: lx0 + 0.75, z: lz0 + 5.65, facing: Math.PI / 2, room: "command" },
      { id: "shelf-command", kind: "shelf", pose: "stand", x: leadRoom.maxX - 1.1, z: lz0 + 4.7, facing: Math.PI / 2, room: "command" },
      // Server room: in the cold aisle, each looking at a rack row's glass fronts.
      { id: "console-e", kind: "board", pose: "stand", x: serverAisleMaxX - 0.55, z: serverRowZ, facing: Math.PI / 2, room: "server" },
      { id: "console-w", kind: "board", pose: "stand", x: serverAisleMinX + 0.55, z: serverRowZ, facing: -Math.PI / 2, room: "server" },
    ] satisfies Spot[] : [
      // In front of the feature wall's east bookcase.
      { id: "shelf-lead", kind: "shelf", pose: "stand", x: leadRoom.maxX - 1.45, z: topZ + 1.05, facing: Math.PI, room: "lead" },
      // Browsing the wardrobe's jacket rail.
      { id: "shelf-wardrobe", kind: "shelf", pose: "stand", x: wcx - 1.0, z: topZ + 1.3, facing: Math.PI, room: "wardrobe" },
      ...lobbySpots(receptionRoom),
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
  const elevator = furniture.find((f) => f.id === "elevator")!;
  // The spawn point's ring surrounds the terminal; "walk there" and newcomers stand south of it, facing the screen.
  const arrival: Point = { x: plaza.x, z: plaza.z + SPAWN.approach };
  const spawnStop: Checkpoint = { id: coding ? "launch" : "spawn", room: "floor", x: plaza.x, z: plaza.z, radius: SPAWN.reach,
    tokenY: SPAWN.tokenY, approach: arrival };
  // The elevator's doors face east into the lobby; its checkpoint is the floor in front of them.
  const elevatorStop: Checkpoint = { id: "elevator", room: "reception", x: elevator.x + 0.95, z: elevator.z, radius: 1.0 };
  const breakStop: Checkpoint = { id: "break", room: "break", x: bx0 + 5.6, z: bz0 + 2.6, radius: 1.8 };
  // Mission Control's stop is a ring round the desk, usable from every side; "walk there" ends in front of the desk.
  const missionStop: Checkpoint = { id: "mission", room: "command", x: commandDeskAt.x, z: commandDeskAt.z - 0.3, radius: COMMAND_REACH,
    tokenY: 2.25, approach: { x: commandDeskAt.x, z: commandDeskAt.z + COMMAND_DESK.d / 2 + 0.55 } };
  const board = furniture.find((f) => f.id === AGENT_BOARD_ID);
  const checkpoints: Checkpoint[] = coding ? [spawnStop, missionStop, elevatorStop, breakStop] : [
    spawnStop,
    { id: "create", room: "reception", x: reception.x, z: reception.z + 1.7, radius: 1.2 },
    ...(board ? [{ id: "manage", room: "reception", x: board.x, z: board.z + 1.4, radius: 1.1 } satisfies Checkpoint] : []),
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
    // Bench desk chairs and the meeting chairs: nobody walks through a chair.
    ...desks.filter((d) => !d.size).map((d) => chairRect(seatOf(d))),
    ...spots.filter((s) => s.kind === "meeting").map((s) => chairRect(s)),
    // Department sign walls along each department's north edge.
    ...departments.map((d) => ({ minX: d.minX + 0.2, maxX: d.maxX - 0.2, minZ: d.minZ + 0.02, maxZ: d.minZ + 0.2 })),
    ...furniture.filter((f) => FURNITURE_SIZE[f.kind].solid).map(footprint),
    ...(coding ? commandDeskObstacles(commandDeskAt) : []),
    // Planter boxes at the bench ends (walnut on the coding floor, oak on the agents floor).
    ...departments.flatMap(benchPlanters).map((p) => p.rect),
    // The coding floor's tree planters, bookcases and troughs along the windows.
    ...(coding ? codingAmbience({ departments, rooms }) : []).map(({ minX, maxX, minZ, maxZ }) => ({ minX, maxX, minZ, maxZ })),
    // The agents floor's olive trees, window benches and planters along the windows.
    ...(coding ? [] : agentsAmbience({ departments, rooms })).map(({ minX, maxX, minZ, maxZ }) => ({ minX, maxX, minZ, maxZ })),
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
    // Its chair faces south, towards the door: seatOf() puts the person north of the desk.
    ...(coding ? { command: { id: "command", x: commandDeskAt.x, z: commandDeskAt.z, facing: "south" as const, agentId: null,
      size: { w: COMMAND_DESK.w, d: COMMAND_DESK.d } } } : {}),
    spawn: { x: rx0 + 1.4, z: bottomZ - 2.6 },
    arrival,
    floor,
    bounds: { minX: minX - EDGE, maxX: maxX + EDGE, minZ: topZ - EDGE, maxZ: bottomZ + EDGE },
  };
}

/** The lobby lounge's sofa, relative to the reception room's west and north edges. */
const LOBBY_LOUNGE = { dx: 6.0, dz: 5.35 };
/** The agents floor's Agent board totem; the "manage" checkpoint stands in front of it. */
const AGENT_BOARD_ID = "agent-board";

/**
 * The agents floor's lobby round the reception desk and the elevator: the
 * brand wall on the west edge north of the elevator, an olive tree at each
 * end of the room and one by the lounge, the award vitrine south of the
 * elevator with the mat in front of its doors, and the waiting lounge — a
 * sofa facing south, armchairs either side of the coffee table on a rug. The
 * walk from the elevator north past the desk and east to the break room
 * stays clear.
 */
function lobbyFurniture(room: Rect): Furniture[] {
  const x0 = room.minX, z0 = room.minZ;
  const lx = x0 + LOBBY_LOUNGE.dx, lz = z0 + LOBBY_LOUNGE.dz;
  const piece = (id: string, kind: FurnitureKind, x: number, z: number, rotationY = 0, size?: { w: number; d: number }): Furniture =>
    ({ id, kind, x, z, rotationY, room: "reception", ...(size ? { size } : {}) });
  return [
    // The Agent board: a touch-screen totem in the lobby's north-east corner.
    piece(AGENT_BOARD_ID, "agentTotem", room.maxX - 2.2, z0 + 2.2),
    piece("lobby-brand-wall", "brandWall", x0 + 0.22, z0 + 2.55, Math.PI / 2),
    piece("lobby-olive-nw", "oliveTree", x0 + 0.65, z0 + 0.35),
    piece("lobby-olive-se", "oliveTree", room.maxX - 0.75, room.maxZ - 0.7),
    piece("lobby-olive-s", "oliveTree", x0 + 2.7, room.maxZ - 0.55),
    piece("lobby-awards", "awardCase", x0 + 0.26, room.maxZ - 0.62, Math.PI / 2),
    piece("lobby-mat", "entranceMat", x0 + 1.3, room.maxZ - 2.6, 0, { w: 1.1, d: 1.9 }),
    piece("lobby-rug", "lobbyRug", lx, lz + 0.75, 0, { w: 4.8, d: 2.9 }),
    piece("lobby-sofa", "lobbySofa", lx, lz),
    piece("lobby-table", "lobbyTable", lx, lz + 1.05),
    piece("lobby-chair-w", "lobbyArmchair", lx - 1.8, lz + 1.1, Math.PI / 2),
    piece("lobby-chair-e", "lobbyArmchair", lx + 1.8, lz + 1.1, -Math.PI / 2),
    piece("lobby-side-table", "sideTable", lx - 1.5, lz - 0.05),
    piece("lobby-lamp", "lobbyLamp", lx + 1.5, lz - 0.1),
  ];
}

/** Seats in the lobby lounge: two on the sofa (facing south), one in each armchair (facing the table). */
function lobbySpots(room: Rect): Spot[] {
  const lx = room.minX + LOBBY_LOUNGE.dx, lz = room.minZ + LOBBY_LOUNGE.dz;
  return [
    { id: "couch-lobby-1", kind: "couch", pose: "sit", x: lx - 0.55, z: lz + 0.15, facing: 0, room: "reception" },
    { id: "couch-lobby-2", kind: "couch", pose: "sit", x: lx + 0.55, z: lz + 0.15, facing: 0, room: "reception" },
    { id: "couch-lobby-w", kind: "couch", pose: "sit", x: lx - 1.78, z: lz + 1.1, facing: Math.PI / 2, room: "reception" },
    { id: "couch-lobby-e", kind: "couch", pose: "sit", x: lx + 1.78, z: lz + 1.1, facing: -Math.PI / 2, room: "reception" },
  ];
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
