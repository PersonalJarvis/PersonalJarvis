/**
 * What each figure on the office map does, decided client-side and
 * deterministically from a seeded random stream.
 *
 * The map is a projection of the roster: it never starts work, and idle life
 * (coffee, couch, wandering) costs zero model calls. Working, waiting and
 * paused agents mirror their real run state; only idle agents pick hobbies.
 */
import type { AgentRunState } from "../data";
import { seatOf, standOf, type DeskSlot, type OfficeLayout, type Point, type Rect, type Spot, type SpotKind } from "./officeLayout";
import { nearestWalkable, randomWalkablePoint, type NavGrid } from "./officeNav";

export type Pose = "sit" | "stand" | "sleep" | "wave" | "talk" | "work";

export type ActivityKind =
  | "work" | "wait" | "desk" | "coffee" | "couch" | "nap" | "window" | "shelf" | "arcade"
  | "cooler" | "beanbag" | "meeting" | "board" | "wander" | "visit" | "called";

export interface Plan {
  kind: ActivityKind;
  target: Point;
  /** Heading to settle into on arrival; null = keep the walking heading. */
  facing: number | null;
  pose: Pose;
  dwellMs: number;
  spotId: string | null;
  /** Agent id being visited. */
  with: string | null;
}

/** Deterministic PRNG (mulberry32) seeded from a string hash. */
export function createRng(seed: string): () => number {
  // FNV-1a over the UTF-16 code units, then one avalanche round.
  let h = 0x811c9dc5;
  for (let i = 0; i < seed.length; i += 1) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  h ^= h >>> 16; h = Math.imul(h, 0x85ebca6b); h ^= h >>> 13;
  let state = h >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Exclusive claims on hangout spots: one agent per spot, one spot per agent. */
export class SpotBook {
  private bySpot = new Map<string, string>();
  private byAgent = new Map<string, string>();

  /** Claim a spot for an agent; releases the agent's previous spot. False if another agent holds it. */
  claim(spotId: string, agentId: string): boolean {
    const holder = this.bySpot.get(spotId);
    if (holder !== undefined && holder !== agentId) return false;
    this.release(agentId);
    this.bySpot.set(spotId, agentId);
    this.byAgent.set(agentId, spotId);
    return true;
  }

  release(agentId: string): void {
    const spotId = this.byAgent.get(agentId);
    if (spotId === undefined) return;
    this.byAgent.delete(agentId);
    if (this.bySpot.get(spotId) === agentId) this.bySpot.delete(spotId);
  }

  /** Keep only claims on spots that still exist (after the floor plan was rebuilt). */
  retain(spotIds: ReadonlySet<string>): void {
    for (const [spotId, agentId] of [...this.bySpot]) {
      if (!spotIds.has(spotId)) { this.bySpot.delete(spotId); this.byAgent.delete(agentId); }
    }
  }

  holder(spotId: string): string | null {
    return this.bySpot.get(spotId) ?? null;
  }

  isFree(spotId: string): boolean {
    return !this.bySpot.has(spotId);
  }
}

export interface PlanInput {
  agentId: string;
  state: AgentRunState;
  desk: DeskSlot | null;
  layout: OfficeLayout;
  grid: NavGrid;
  rng: () => number;
  book: SpotBook;
  previous: ActivityKind | null;
  workingColleagues: { agentId: string; desk: DeskSlot }[];
  calledTo: Point | null;
  /** Where the agent currently is; lets a called agent stop on its own side of the caller. */
  position?: Point | null;
  /** A team meeting's chair for this agent (a `meeting-*` spot id); it sits there instead of standing by `calledTo`. */
  calledSeat?: string | null;
}

/** Idle activities and their relative weights. */
const IDLE_WEIGHTS: readonly [ActivityKind, number][] = [
  ["couch", 22], ["coffee", 18], ["window", 12], ["cooler", 8], ["arcade", 8], ["shelf", 7], ["beanbag", 8],
  ["meeting", 5], ["board", 4], ["wander", 10], ["visit", 6], ["desk", 10],
];

const SPOT_KIND: Partial<Record<ActivityKind, SpotKind>> = {
  couch: "couch", coffee: "coffee", window: "window", cooler: "cooler", arcade: "arcade",
  shelf: "shelf", beanbag: "beanbag", meeting: "meeting", board: "board",
};

const CALLED_DISTANCE = 1.2;
const CALLED_DWELL_MS = 15000;

function between(rng: () => number, minMs: number, maxMs: number): number {
  return Math.round(minMs + rng() * (maxMs - minMs));
}

function pick<T>(rng: () => number, items: readonly T[]): T {
  return items[Math.min(items.length - 1, Math.floor(rng() * items.length))];
}

function poseOfSpot(spot: Spot): Pose {
  return spot.pose;
}

/** Claim a random free spot matching `filter`; null when all are taken. */
function claimSpot(input: PlanInput, filter: (s: Spot) => boolean): Spot | null {
  const free = input.layout.spots.filter((s) => filter(s) && input.book.isFree(s.id));
  if (free.length === 0) return null;
  const spot = pick(input.rng, free);
  input.book.claim(spot.id, input.agentId);
  return spot;
}

/** The open office between the north and south strips: the departments' bounding box. */
function openOffice(layout: OfficeLayout): Rect {
  if (layout.departments.length === 0) return layout.floor;
  return {
    minX: Math.min(...layout.departments.map((d) => d.minX)),
    maxX: Math.max(...layout.departments.map((d) => d.maxX)),
    minZ: Math.min(...layout.departments.map((d) => d.minZ)),
    maxZ: Math.max(...layout.departments.map((d) => d.maxZ)),
  };
}

function wanderPlan(input: PlanInput, pose: Pose, dwellMs: number): Plan {
  const target = randomWalkablePoint(input.grid, input.rng, openOffice(input.layout))
    ?? randomWalkablePoint(input.grid, input.rng)
    ?? input.layout.spawn;
  return { kind: "wander", target, facing: null, pose, dwellMs, spotId: null, with: null };
}

function spotPlan(kind: ActivityKind, spot: Spot, pose: Pose, dwellMs: number): Plan {
  return { kind, target: { x: spot.x, z: spot.z }, facing: spot.facing, pose, dwellMs, spotId: spot.id, with: null };
}

function calledPlan(input: PlanInput, caller: Point): Plan {
  const from = input.position;
  let angle: number;
  if (from && Math.hypot(from.x - caller.x, from.z - caller.z) > 1e-3) angle = Math.atan2(from.x - caller.x, from.z - caller.z);
  else angle = input.rng() * Math.PI * 2;
  const wanted = { x: caller.x + Math.sin(angle) * CALLED_DISTANCE, z: caller.z + Math.cos(angle) * CALLED_DISTANCE };
  const target = nearestWalkable(input.grid, wanted) ?? nearestWalkable(input.grid, caller) ?? wanted;
  const facing = Math.atan2(caller.x - target.x, caller.z - target.z);
  return { kind: "called", target, facing, pose: "talk", dwellMs: CALLED_DWELL_MS, spotId: null, with: null };
}

function idlePlan(input: PlanInput): Plan {
  const { rng, desk, previous } = input;
  const colleagues = input.workingColleagues.filter((c) => c.agentId !== input.agentId);
  const available = IDLE_WEIGHTS.filter(([kind]) => {
    if (kind === "visit") return colleagues.length > 0;
    if (kind === "desk") return desk !== null;
    return true;
  });
  const options = available.length > 1 ? available.filter(([kind]) => kind !== previous) : available;
  const total = options.reduce((sum, [, w]) => sum + w, 0);
  let roll = rng() * total;
  let kind: ActivityKind = options[options.length - 1][0];
  for (const [k, w] of options) {
    if (roll < w) { kind = k; break; }
    roll -= w;
  }

  if (kind === "wander") return wanderPlan(input, "stand", between(rng, 6000, 12000));
  if (kind === "desk" && desk) {
    const seat = seatOf(desk);
    return { kind: "desk", target: { x: seat.x, z: seat.z }, facing: seat.facing, pose: "sit", dwellMs: between(rng, 15000, 30000), spotId: null, with: null };
  }
  if (kind === "visit") {
    const colleague = pick(rng, colleagues);
    const seat = seatOf(colleague.desk);
    const stand = standOf(colleague.desk);
    return {
      kind: "visit", target: { x: stand.x, z: stand.z }, facing: Math.atan2(seat.x - stand.x, seat.z - stand.z),
      pose: "talk", dwellMs: between(rng, 8000, 15000), spotId: null, with: colleague.agentId,
    };
  }
  const spotKind = SPOT_KIND[kind];
  const spot = spotKind ? claimSpot(input, (s) => s.kind === spotKind) : null;
  if (!spot) return wanderPlan(input, "stand", between(rng, 6000, 12000));
  if (kind === "couch") return spotPlan(kind, spot, "sit", between(rng, 20000, 45000));
  return spotPlan(kind, spot, poseOfSpot(spot) === "sleep" ? "sit" : poseOfSpot(spot), between(rng, 8000, 25000));
}

/** Decide the next activity for one agent. Releases its previous spot claim first. */
export function planFor(input: PlanInput): Plan {
  const { agentId, state, desk, book } = input;
  book.release(agentId);

  // A working agent stays at its screen: the map mirrors real work and never interrupts it, not even visually.
  if (input.calledTo && input.state !== "working") {
    // A meeting seats its members: the assigned chair, else any free chair at the table, else standing by it.
    if (input.calledSeat) {
      const assigned = input.layout.spots.find((s) => s.id === input.calledSeat);
      if (assigned && book.claim(assigned.id, agentId)) return spotPlan("meeting", assigned, "sit", Infinity);
      const other = claimSpot(input, (s) => s.kind === "meeting");
      if (other) return spotPlan("meeting", other, "sit", Infinity);
    }
    return calledPlan(input, input.calledTo);
  }

  if (state === "working") {
    if (desk) {
      const seat = seatOf(desk);
      return { kind: "work", target: { x: seat.x, z: seat.z }, facing: seat.facing, pose: "work", dwellMs: Infinity, spotId: null, with: null };
    }
    const spot = claimSpot(input, (s) => s.kind === "window");
    if (spot) return spotPlan("work", spot, "work", Infinity);
    return { ...wanderPlan(input, "work", Infinity), kind: "work" };
  }

  if (state === "waiting") {
    if (desk) {
      const stand = standOf(desk);
      return { kind: "wait", target: { x: stand.x, z: stand.z }, facing: stand.facing, pose: "wave", dwellMs: Infinity, spotId: null, with: null };
    }
    const spot = claimSpot(input, (s) => s.pose === "stand");
    if (spot) return spotPlan("wait", spot, "wave", Infinity);
    return { ...wanderPlan(input, "wave", Infinity), kind: "wait" };
  }

  if (state === "paused") {
    const bed = claimSpot(input, (s) => s.pose === "sleep");
    if (bed) return spotPlan("nap", bed, "sleep", Infinity);
    const couch = claimSpot(input, (s) => s.kind === "couch");
    if (couch) return spotPlan("nap", couch, "sit", Infinity);
    if (desk) {
      const seat = seatOf(desk);
      return { kind: "nap", target: { x: seat.x, z: seat.z }, facing: seat.facing, pose: "sit", dwellMs: Infinity, spotId: null, with: null };
    }
    return { ...wanderPlan(input, "sit", Infinity), kind: "nap" };
  }

  return idlePlan(input);
}
