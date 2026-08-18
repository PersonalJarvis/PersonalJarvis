/**
 * A soft pull toward the origin, for force layouts that would otherwise let a
 * node drift out of the world.
 *
 * Why this has to exist: a node with no links feels repulsion and nothing
 * else. It accelerates away from the cluster until it is out of every other
 * node's reach, and then it simply stays there. On a flat canvas that is a dot
 * near the edge; in a 3D scene it is worse, because "frame everything" is how
 * the camera decides where to sit — one stray node ten cluster-widths out and
 * the whole network is rendered as a marble in the middle of an empty room.
 * A vault with a single unlinked page is enough to trigger it.
 *
 * The library's own `center` force does not solve this: it translates every
 * node so the CENTROID lands on the origin, which moves the stray node and the
 * cluster together and changes nothing about the distance between them. What
 * is needed is a per-node pull, which is what this is — d3's `forceX(0)`,
 * `forceY(0)` and `forceZ(0)` in one pass, written out rather than pulled in
 * as a dependency because it is nine lines of arithmetic.
 *
 * Strength is deliberately tiny. It has to be weaker than the repulsion
 * between neighbours (or the layout collapses into a ball) while still being
 * the only force acting a long way out, where repulsion has fallen off. That
 * is what bounds the world without compressing it.
 */

/** The mutable slice of a node a force integrates over. */
interface SimNode {
  x?: number;
  y?: number;
  z?: number;
  vx?: number;
  vy?: number;
  vz?: number;
}

/** A d3-force-shaped force: callable, with an `initialize` hook. */
export interface CentringForce {
  (alpha: number): void;
  initialize: (nodes: SimNode[]) => void;
}

/**
 * Build the pull.
 *
 * @param strength velocity change per unit of distance per tick, scaled by the
 *   simulation's alpha so it fades out with the rest of the layout.
 */
export function createCentringForce(strength: number): CentringForce {
  let nodes: SimNode[] = [];

  const force = ((alpha: number): void => {
    const k = strength * alpha;
    if (k === 0) return;
    for (const node of nodes) {
      node.vx = (node.vx ?? 0) - (node.x ?? 0) * k;
      node.vy = (node.vy ?? 0) - (node.y ?? 0) * k;
      node.vz = (node.vz ?? 0) - (node.z ?? 0) * k;
    }
  }) as CentringForce;

  // d3 calls this whenever the simulation's node array is (re)assigned, which
  // is also how a fresh data generation reaches an already-registered force.
  force.initialize = (next: SimNode[]): void => {
    nodes = next ?? [];
  };

  return force;
}

/**
 * Strength used by both 3D maps.
 *
 * Tuned against a real vault of 59 pages whose one unlinked page sat far
 * enough out to shrink the entire network to a tenth of the frame. Paired with
 * a bounded repulsion radius it now settles just outside the cluster, and the
 * connected core keeps the spread the repulsion gave it.
 */
export const CENTRING_STRENGTH = 0.04;

// ----------------------------------------------------------------------
// Liveliness — every page but the pivot keeps moving on its own
// ----------------------------------------------------------------------

/**
 * A node the liveliness force can move: the simulation's position, an id to
 * derive a stable rhythm from, and the private slot where the force keeps
 * the offset it has currently applied.
 */
export interface LivelyNode extends SimNode {
  id?: string | number;
  /** The displacement currently applied, so the next tick can replace it. */
  __lively?: { x: number; y: number; z: number };
}

export interface LivelinessForce {
  (alpha: number): void;
  initialize: (nodes: LivelyNode[]) => void;
}

export interface LivelinessOptions {
  /** Milliseconds; injected so the motion is testable without a clock. */
  now: () => number;
  /** True for the node that must not move — the pivot the map turns around. */
  isPinned: (node: LivelyNode) => boolean;
  /** Peak vertical displacement, graph units. */
  amplitude?: number;
  /** Radius of the small horizontal loop each node adds to its bob. */
  sway?: number;
  /** Shortest and longest full bob, milliseconds — each node gets its own. */
  periodMs?: [number, number];
}

/** Bob height, in graph units. Link distance is 85, a node radius 3–8. */
export const LIVELINESS_AMPLITUDE = 9;
/** Radius of the sideways loop. */
export const LIVELINESS_SWAY = 3;
/** One full bob takes between these — brisk, and never the same for two pages. */
export const LIVELINESS_PERIOD_MS: [number, number] = [2_600, 4_600];

/** A small, stable hash of a node id — the same page always gets the same rhythm. */
export function rhythmSeed(id: string | number | undefined): number {
  const text = String(id ?? "");
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967295; // 0..1
}

/**
 * The motion, on top of whatever the layout does.
 *
 * The maintainer's ask (2026-08-18): the pages must not turn as one rigid
 * body around the pivot — every page but the pivot should move on its own,
 * up and down, quickly enough not to be boring. So each node gets a bob in
 * `y` and a small loop in `x`/`z`, with a period and a phase of its own,
 * derived from its id so a page keeps its rhythm across re-renders and two
 * neighbours never move in step.
 *
 * It is applied as a DELTA: the force remembers the offset it applied last
 * tick and swaps it for this tick's, so it composes with the layout at any
 * alpha — while the pages are still flying apart after new data, and once
 * they have settled and alpha has gone to zero (this force ignores alpha on
 * purpose; the layout's forces fade out, the life must not). The pivot is
 * left exactly where the layout puts it, so the point the map turns around
 * stays a point.
 */
export function createLivelinessForce(options: LivelinessOptions): LivelinessForce {
  const amplitude = options.amplitude ?? LIVELINESS_AMPLITUDE;
  const sway = options.sway ?? LIVELINESS_SWAY;
  const [minPeriod, maxPeriod] = options.periodMs ?? LIVELINESS_PERIOD_MS;
  let nodes: LivelyNode[] = [];

  const force = ((_alpha: number): void => {
    const t = options.now();
    for (const node of nodes) {
      const previous = node.__lively ?? { x: 0, y: 0, z: 0 };
      let next = { x: 0, y: 0, z: 0 };
      if (!options.isPinned(node)) {
        const seed = rhythmSeed(node.id);
        const period = minPeriod + (maxPeriod - minPeriod) * seed;
        const phase = seed * Math.PI * 2;
        const angle = (t / period) * Math.PI * 2 + phase;
        next = {
          x: Math.cos(angle * 0.5) * sway,
          y: Math.sin(angle) * amplitude,
          z: Math.sin(angle * 0.5) * sway,
        };
      }
      node.x = (node.x ?? 0) + (next.x - previous.x);
      node.y = (node.y ?? 0) + (next.y - previous.y);
      node.z = (node.z ?? 0) + (next.z - previous.z);
      node.__lively = next;
    }
  }) as LivelinessForce;

  force.initialize = (next: LivelyNode[]): void => {
    nodes = next ?? [];
  };

  return force;
}
