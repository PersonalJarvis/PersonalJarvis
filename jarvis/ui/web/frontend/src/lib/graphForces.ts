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
