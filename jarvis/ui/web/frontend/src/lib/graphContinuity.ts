/**
 * Keep a force layout's pages where they are when the data behind it changes.
 *
 * The renderer starts every new data generation from scratch: nodes that
 * arrive without a position are scattered by d3's phyllotaxis and the whole
 * network explodes and re-settles — a "complete snap" on a map that was
 * calm a moment ago. On the deck that happens every time the assistant
 * writes a wiki page (the graph is refetched, and the payload builds fresh
 * node objects). So before a new generation reaches the renderer, this
 * copies each page's position and velocity over from the previous
 * generation's object with the same id, and drops a NEW page next to the
 * pages it links to — where it belongs — instead of at the origin. The
 * simulation then only has to settle the newcomer; everything else stays.
 *
 * Pure: it mutates the node objects it is handed (which are the objects the
 * simulation will integrate over) and touches nothing else.
 */

export interface ContinuityNode {
  id: string;
  x?: number;
  y?: number;
  z?: number;
  vx?: number;
  vy?: number;
  vz?: number;
  /**
   * d3-force pin. When set, the simulation writes `x = fx` (and the same
   * for y/z) every tick and zeroes the velocity — the node cannot drift.
   */
  fx?: number;
  fy?: number;
  fz?: number;
  /** The liveliness force's applied offset — travels with the position. */
  __lively?: { x: number; y: number; z: number };
}

export interface ContinuityLink {
  source: string | { id: string };
  target: string | { id: string };
}

/** How far from its neighbours' middle a newcomer lands, graph units. */
const NEWCOMER_SPREAD = 18;

function idOf(endpoint: string | { id: string }): string {
  return typeof endpoint === "string" ? endpoint : endpoint.id;
}

/** A small deterministic scatter so two newcomers do not land on one spot. */
function scatter(id: string, axis: number): number {
  let h = 2166136261 ^ axis;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) / 4294967295 - 0.5) * 2 * NEWCOMER_SPREAD;
}

/**
 * Carry positions from `previous` onto `next` (matched by id) and seat
 * newcomers next to their neighbours.
 *
 * @returns how many pages kept their place, and how many were seated anew
 */
export function carryOverPositions(
  previous: readonly ContinuityNode[],
  next: ContinuityNode[],
  links: readonly ContinuityLink[],
  fallback: { x?: number; y?: number; z?: number } = {},
): { kept: number; seated: number } {
  // The very first generation is the simulation's to spread — d3's spiral
  // seeding is what the layout was tuned against.
  if (previous.length === 0) return { kept: 0, seated: 0 };

  const before = new Map(previous.map((node) => [node.id, node]));
  let kept = 0;
  const newcomers: ContinuityNode[] = [];

  for (const node of next) {
    const was = before.get(node.id);
    if (was && Number.isFinite(was.x) && Number.isFinite(was.y)) {
      node.x = was.x;
      node.y = was.y;
      node.z = was.z ?? 0;
      node.vx = was.vx ?? 0;
      node.vy = was.vy ?? 0;
      node.vz = was.vz ?? 0;
      if (was.__lively) node.__lively = { ...was.__lively };
      kept += 1;
    } else if (!Number.isFinite(node.x)) {
      newcomers.push(node);
    }
  }
  if (newcomers.length === 0) return { kept, seated: 0 };

  // Newcomers sit at the middle of their already-placed neighbours.
  const placed = new Map(next.filter((n) => Number.isFinite(n.x)).map((n) => [n.id, n]));
  const neighbours = new Map<string, ContinuityNode[]>();
  for (const link of links) {
    const a = idOf(link.source);
    const b = idOf(link.target);
    const pa = placed.get(a);
    const pb = placed.get(b);
    if (pb) (neighbours.get(a) ?? neighbours.set(a, []).get(a)!).push(pb);
    if (pa) (neighbours.get(b) ?? neighbours.set(b, []).get(b)!).push(pa);
  }

  const fx = fallback.x ?? 0;
  const fy = fallback.y ?? 0;
  const fz = fallback.z ?? 0;
  for (const node of newcomers) {
    const near = neighbours.get(node.id) ?? [];
    let x = fx;
    let y = fy;
    let z = fz;
    if (near.length > 0) {
      x = near.reduce((s, n) => s + (n.x ?? 0), 0) / near.length;
      y = near.reduce((s, n) => s + (n.y ?? 0), 0) / near.length;
      z = near.reduce((s, n) => s + (n.z ?? 0), 0) / near.length;
    }
    node.x = x + scatter(node.id, 1);
    node.y = y + scatter(node.id, 2);
    node.z = z + scatter(node.id, 3);
    node.vx = 0;
    node.vy = 0;
    node.vz = 0;
  }
  return { kept, seated: newcomers.length };
}

/**
 * Nail the hub to the origin so the map turns around a point that does not
 * move. Other pages are translated with it, so a layout they already have is
 * kept; d3-force's `fx`/`fy`/`fz` then holds the hub there every tick.
 *
 * Without this the charge and link forces keep shoving the hub around, the
 * camera eases after it, and the main page wanders off the middle of the
 * panel — the opposite of what the orbit is for.
 *
 * @returns true when a hub was found and pinned
 */
export function pinPivotAtOrigin(
  nodes: ContinuityNode[],
  pivotId: string | null | undefined,
): boolean {
  if (!pivotId) return false;
  const pivot = nodes.find((node) => node.id === pivotId);
  if (!pivot) return false;

  const dx = Number.isFinite(pivot.x) ? (pivot.x as number) : 0;
  const dy = Number.isFinite(pivot.y) ? (pivot.y as number) : 0;
  const dz = Number.isFinite(pivot.z) ? (pivot.z as number) : 0;

  if (dx !== 0 || dy !== 0 || dz !== 0) {
    for (const node of nodes) {
      if (Number.isFinite(node.x)) node.x = (node.x as number) - dx;
      if (Number.isFinite(node.y)) node.y = (node.y as number) - dy;
      if (Number.isFinite(node.z)) node.z = (node.z as number) - dz;
    }
  }

  pivot.x = 0;
  pivot.y = 0;
  pivot.z = 0;
  pivot.vx = 0;
  pivot.vy = 0;
  pivot.vz = 0;
  pivot.fx = 0;
  pivot.fy = 0;
  pivot.fz = 0;
  return true;
}
