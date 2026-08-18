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
// Liveliness — every page but the pivot keeps moving, together
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
  /**
   * Where the network breathes from — the pivot's position when there is
   * one, else the origin. Read every tick, so a moving pivot is followed.
   */
  centre?: () => Vec3Like;
  /** Peak height of the wave, graph units. */
  amplitude?: number;
  /** One full wave cycle at a fixed point, milliseconds. */
  wavePeriodMs?: number;
  /** Distance between two crests, graph units. */
  wavelength?: number;
  /** Peak in-and-out of the breathing, as a share of the distance from centre. */
  breath?: number;
  /** One full breath, milliseconds. */
  breathPeriodMs?: number;
  /** Peak of each page's own small drift, graph units. */
  wobble?: number;
}

interface Vec3Like {
  x?: number;
  y?: number;
  z?: number;
}

/**
 * The motion, on top of whatever the layout does.
 *
 * Three layers, all slow sinusoids, so nothing ever jumps:
 *  - a WAVE rolls through the network: every page bobs up and down, and
 *    its phase depends on where it stands, so neighbours move almost
 *    together and a crest travels across the map — a thing to follow with
 *    the eye rather than noise to be annoyed by;
 *  - the whole network BREATHES: pages ease a little away from the pivot
 *    and back, together;
 *  - each page adds a small WOBBLE of its own, so no two move exactly alike.
 *
 * The first cut gave every page an independent rhythm; the maintainer read
 * that as random and jerky (2026-08-18). Coherence is what makes it calm.
 *
 * It is applied as a DELTA: the force remembers the offset it applied last
 * tick and swaps it for this tick's, so it composes with the layout at any
 * alpha — while the pages are still flying apart after new data, and once
 * they have settled and alpha has gone to zero (this force ignores alpha on
 * purpose; the layout's forces fade out, the life must not). The pivot is
 * left exactly where the layout puts it, so the point the map turns around
 * stays a point.
 */
export const LIVELINESS_AMPLITUDE = 10;
export const LIVELINESS_WAVE_PERIOD_MS = 5_200;
export const LIVELINESS_WAVELENGTH = 240;
export const LIVELINESS_BREATH = 0.035;
export const LIVELINESS_BREATH_PERIOD_MS = 9_000;
export const LIVELINESS_WOBBLE = 1.6;

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

/** The wave travels along this direction in the horizontal plane. */
const WAVE_DIR = { x: Math.cos(0.6), z: Math.sin(0.6) };

/**
 * The offset the force wants for one node at time `t`, given the node's
 * layout position `base` (its position minus the current offset). Pure, so
 * the shape of the motion is testable on its own.
 */
export function livelyOffset(
  base: Vec3Like,
  seed: number,
  t: number,
  centre: Vec3Like,
  options: Required<Pick<LivelinessOptions, "amplitude" | "wavePeriodMs" | "wavelength" | "breath" | "breathPeriodMs" | "wobble">>,
): { x: number; y: number; z: number } {
  const bx = base.x ?? 0;
  const by = base.y ?? 0;
  const bz = base.z ?? 0;
  const cx = centre.x ?? 0;
  const cy = centre.y ?? 0;
  const cz = centre.z ?? 0;

  // The wave: phase from position along the travel direction, plus a small
  // personal offset so the crest is not a ruler-straight line.
  const along = (bx - cx) * WAVE_DIR.x + (bz - cz) * WAVE_DIR.z;
  const wavePhase =
    (t / options.wavePeriodMs) * Math.PI * 2 -
    (along / options.wavelength) * Math.PI * 2 +
    (seed - 0.5) * 0.9;
  const wave = Math.sin(wavePhase) * options.amplitude;

  // The breath: radial, shared by all, scaled by how far out the page sits.
  const dx = bx - cx;
  const dy = by - cy;
  const dz = bz - cz;
  const breathScale = Math.sin((t / options.breathPeriodMs) * Math.PI * 2) * options.breath;

  // The wobble: a slow personal loop, its period from the seed (7–11 s).
  const wobblePeriod = 7_000 + seed * 4_000;
  const wobbleAngle = (t / wobblePeriod) * Math.PI * 2 + seed * Math.PI * 2;

  return {
    x: dx * breathScale + Math.cos(wobbleAngle) * options.wobble,
    y: wave + dy * breathScale,
    z: dz * breathScale + Math.sin(wobbleAngle) * options.wobble,
  };
}

export function createLivelinessForce(options: LivelinessOptions): LivelinessForce {
  const shape = {
    amplitude: options.amplitude ?? LIVELINESS_AMPLITUDE,
    wavePeriodMs: options.wavePeriodMs ?? LIVELINESS_WAVE_PERIOD_MS,
    wavelength: options.wavelength ?? LIVELINESS_WAVELENGTH,
    breath: options.breath ?? LIVELINESS_BREATH,
    breathPeriodMs: options.breathPeriodMs ?? LIVELINESS_BREATH_PERIOD_MS,
    wobble: options.wobble ?? LIVELINESS_WOBBLE,
  };
  const centreOf = options.centre ?? (() => ({ x: 0, y: 0, z: 0 }));
  let nodes: LivelyNode[] = [];

  const force = ((_alpha: number): void => {
    const t = options.now();
    const centre = centreOf();
    for (const node of nodes) {
      const previous = node.__lively ?? { x: 0, y: 0, z: 0 };
      let next = { x: 0, y: 0, z: 0 };
      if (!options.isPinned(node)) {
        const base = {
          x: (node.x ?? 0) - previous.x,
          y: (node.y ?? 0) - previous.y,
          z: (node.z ?? 0) - previous.z,
        };
        next = livelyOffset(base, rhythmSeed(node.id), t, centre, shape);
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
