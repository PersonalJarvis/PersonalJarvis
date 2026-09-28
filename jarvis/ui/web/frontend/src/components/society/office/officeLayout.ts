/**
 * The office floor as data: departments, desk slots and who sits where.
 *
 * Pure and deterministic — the same roster always produces the same seating,
 * so an agent never "moves desks" on a refetch. Units are metres, +x east,
 * +z south, origin at the floor centre. The camera looks from the south-east.
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

export interface Department {
  id: string;
  /** Provider family shown on the sign; "" for a spare open-space department. */
  label: string;
  /** Floor rectangle of the department's carpet. */
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
  desks: DeskSlot[];
  /** Carpet tint index into the palette's department colours. */
  tint: number;
}

export interface LeadOffice {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
  desks: DeskSlot[];
}

export interface OfficeLayout {
  departments: Department[];
  lead: LeadOffice;
  lounge: { minX: number; maxX: number; minZ: number; maxZ: number };
  /** The whole walkable floor, railing included. */
  bounds: { minX: number; maxX: number; minZ: number; maxZ: number };
}

/** Desk pitch along a row and the depth of one bench (two rows back to back plus chairs). */
export const DESK_PITCH_X = 1.7;
export const BENCH_DEPTH_Z = 4.2;
/** Half the gap between the two monitors of a back-to-back pair. */
export const DESK_HALF_GAP = 0.45;
export const DESKS_PER_ROW = 4;
const SEATS_PER_BENCH = DESKS_PER_ROW * 2;
const MIN_BENCHES = 1;
const MAX_BENCHES = 4;
const DEPT_MARGIN_X = 1.1;
const DEPT_HEADER_Z = 1.8;
const DEPT_FOOTER_Z = 0.8;
const AISLE = 3.2;
const EDGE = 1.6;
const LOUNGE_DEPTH = 6;
const LEAD_DEPTH = 6;
const COLUMNS = 2;
/** The floor always shows at least this many departments; spare ones are open space with free desks. */
export const MIN_DEPARTMENTS = 4;
/** More departments than this fold into the last one — the floor stays one screen. */
export const MAX_DEPARTMENTS = 6;
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

function deptWidth(): number {
  return DESKS_PER_ROW * DESK_PITCH_X + DEPT_MARGIN_X * 2;
}

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

/** Build the floor for a roster. Seating is stable for an unchanged roster. */
export function buildOfficeLayout(agents: readonly OfficeAgentInput[]): OfficeLayout {
  const groups: { label: string; members: OfficeAgentInput[] }[] = groupDepartments(agents);
  if (groups.length === 1 && groups[0].members.length === 0) groups.length = 0;
  while (groups.length < MIN_DEPARTMENTS) groups.push({ label: "", members: [] });
  const width = deptWidth();
  const rows = Math.ceil(groups.length / COLUMNS);
  const columns = Math.min(COLUMNS, groups.length);
  const totalWidth = columns * width + (columns - 1) * AISLE;
  const minX = -Math.max(totalWidth, 2 * width + AISLE) / 2;

  // Row depth is the deepest department in that row, so rows line up.
  const rowDepths: number[] = [];
  for (let row = 0; row < rows; row += 1) {
    const inRow = groups.slice(row * COLUMNS, row * COLUMNS + COLUMNS);
    rowDepths.push(Math.max(...inRow.map((g) => deptDepth(benchCount(g.members.length)))));
  }
  const floorDepth = LEAD_DEPTH + AISLE + rowDepths.reduce((s, d) => s + d, 0) + (rows - 1) * AISLE + AISLE + LOUNGE_DEPTH;
  const topZ = -floorDepth / 2;
  const floorWidth = Math.max(totalWidth, 2 * width + AISLE);

  const leadAgents = agents.filter((a) => a.tier === "lead").sort(byArrival).slice(0, 2);
  const leadWidth = width;
  const lead: LeadOffice = {
    minX, maxX: minX + leadWidth, minZ: topZ, maxZ: topZ + LEAD_DEPTH,
    desks: [0, 1].map((i) => ({
      id: `lead:${i}`,
      x: minX + leadWidth * (i === 0 ? 0.35 : 0.72),
      z: topZ + LEAD_DEPTH * 0.45,
      facing: "south" as const,
      agentId: leadAgents[i]?.agentId ?? null,
    })),
  };

  const departments: Department[] = [];
  let z = topZ + LEAD_DEPTH + AISLE;
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < COLUMNS; col += 1) {
      const index = row * COLUMNS + col;
      const group = groups[index];
      if (!group) continue;
      const benches = benchCount(group.members.length);
      const dMinX = minX + col * (width + AISLE);
      const id = `dept-${index}`;
      departments.push({
        id, label: group.label, tint: index,
        minX: dMinX, maxX: dMinX + width, minZ: z, maxZ: z + deptDepth(benches),
        desks: layoutDesks(id, dMinX, z, benches, group.members.slice(0, benches * SEATS_PER_BENCH)),
      });
    }
    z += rowDepths[row] + AISLE;
  }

  const lounge = { minX, maxX: minX + floorWidth, minZ: z, maxZ: z + LOUNGE_DEPTH };
  return {
    departments, lead, lounge,
    bounds: { minX: minX - EDGE, maxX: minX + floorWidth + EDGE, minZ: topZ - EDGE, maxZ: lounge.maxZ + EDGE },
  };
}

/** Every desk on the floor, lead office first. */
export function allDesks(layout: OfficeLayout): DeskSlot[] {
  return [...layout.lead.desks, ...layout.departments.flatMap((d) => d.desks)];
}

export interface StatusCounts { working: number; idle: number; waiting: number; paused: number }

export function countStates(agents: readonly Pick<OfficeAgentInput, "state">[]): StatusCounts {
  const counts: StatusCounts = { working: 0, idle: 0, waiting: 0, paused: 0 };
  for (const agent of agents) counts[agent.state] += 1;
  return counts;
}
