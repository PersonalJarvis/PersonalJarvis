/**
 * Seat a memory map like a solar system.
 *
 * The hub is the sun (already pinned at the origin). Every other page sits on
 * a SHELL whose radius is how many wikilinks it is from that sun — neighbours
 * close in, strangers out in the kuiper belt. Angles and a little eccentricity
 * come from the page id, so the same vault always draws the same sky and two
 * neighbours never land on one spot.
 *
 * This is a seating, not a track. Nothing here locks a page to a circle; the
 * shell and orbital forces (graphForces) keep them near their band while they
 * still breathe and wobble. Rigid rails are the thing the maintainer asked
 * us not to build.
 */

import { rhythmSeed } from "@/lib/graphForces";

export interface OrbitalNode {
  id: string;
  x?: number;
  y?: number;
  z?: number;
}

export interface OrbitalLink {
  source: string | { id: string };
  target: string | { id: string };
}

/** Graph units. Index is hop-count from the hub; 0 is the sun itself. */
export const SHELL_RADIUS = [0, 72, 128, 184, 236] as const;

/** Isolated pages sit out here, like a comet that never fell in. */
export const KUIPER_RADIUS = 280;

function idOf(endpoint: string | { id: string }): string {
  return typeof endpoint === "string" ? endpoint : endpoint.id;
}

/** Hop-count from the hub. Unreachable pages are `Infinity`. The hub is 0. */
export function hopsFromHub(
  nodeIds: readonly string[],
  links: readonly OrbitalLink[],
  hubId: string,
): Map<string, number> {
  const hops = new Map<string, number>();
  const known = new Set(nodeIds);
  if (!known.has(hubId)) {
    for (const id of nodeIds) hops.set(id, Number.POSITIVE_INFINITY);
    return hops;
  }

  const adj = new Map<string, string[]>();
  for (const id of nodeIds) adj.set(id, []);
  for (const link of links) {
    const a = idOf(link.source);
    const b = idOf(link.target);
    if (!known.has(a) || !known.has(b) || a === b) continue;
    adj.get(a)!.push(b);
    adj.get(b)!.push(a);
  }

  hops.set(hubId, 0);
  const queue = [hubId];
  for (let i = 0; i < queue.length; i++) {
    const here = queue[i];
    const nextHop = (hops.get(here) ?? 0) + 1;
    for (const neighbour of adj.get(here) ?? []) {
      if (hops.has(neighbour)) continue;
      hops.set(neighbour, nextHop);
      queue.push(neighbour);
    }
  }
  for (const id of nodeIds) {
    if (!hops.has(id)) hops.set(id, Number.POSITIVE_INFINITY);
  }
  return hops;
}

/** The shell a page of this hop belongs on. */
export function shellRadius(hop: number): number {
  if (hop === 0) return 0;
  if (!Number.isFinite(hop) || hop >= SHELL_RADIUS.length) return KUIPER_RADIUS;
  return SHELL_RADIUS[hop] ?? KUIPER_RADIUS;
}

/** The distinct shell radii we actually have pages on — used to draw dust rings. */
export function occupiedShells(
  hops: ReadonlyMap<string, number>,
  hubId: string,
): number[] {
  const seen = new Set<number>();
  for (const [id, hop] of hops) {
    if (id === hubId) continue;
    seen.add(shellRadius(hop));
  }
  return [...seen].sort((a, b) => a - b);
}

export interface OrbitPose {
  x: number;
  y: number;
  z: number;
}

/**
 * Where one page sits on its shell. Pure: the same (id, hop, index, count)
 * always produces the same point.
 *
 * Radius is jittered so the band is a belt, not a wire. Inclination is small
 * so the system has volume without standing on its end.
 */
export function orbitPose(
  id: string,
  hop: number,
  index: number,
  count: number,
): OrbitPose {
  const seed = rhythmSeed(id);
  const seedB = rhythmSeed(`${id}\0`);
  const radius = shellRadius(hop) * (0.88 + seed * 0.24);
  const spread = Math.max(1, count);
  const theta = (Math.PI * 2 * index) / spread + (seed - 0.5) * 0.8;
  const lift = Math.sin((seedB - 0.5) * 0.55) * radius * 0.38;
  return {
    x: Math.cos(theta) * radius,
    y: lift,
    z: Math.sin(theta) * radius,
  };
}

/** Pull a placed page onto its shell, keeping the angle it already has. */
export function snapToShell(node: OrbitalNode, hop: number): void {
  const seed = rhythmSeed(node.id);
  const target = shellRadius(hop) * (0.88 + seed * 0.24);
  const x = node.x ?? 0;
  const z = node.z ?? 0;
  const reach = Math.hypot(x, z);
  if (reach < 1e-6) {
    const pose = orbitPose(node.id, hop, 0, 1);
    node.x = pose.x;
    node.y = pose.y;
    node.z = pose.z;
    return;
  }
  const scale = target / reach;
  node.x = x * scale;
  node.z = z * scale;
  if (!Number.isFinite(node.y)) node.y = 0;
}

/**
 * Place every page but the hub onto its shell. The hub is left at the origin.
 *
 * @returns how many pages were seated
 */
export function seatAllOnShells(
  nodes: OrbitalNode[],
  links: readonly OrbitalLink[],
  hubId: string,
): number {
  const hops = hopsFromHub(
    nodes.map((node) => node.id),
    links,
    hubId,
  );
  const buckets = new Map<number, OrbitalNode[]>();
  for (const node of nodes) {
    if (node.id === hubId) {
      node.x = 0;
      node.y = 0;
      node.z = 0;
      continue;
    }
    const hop = hops.get(node.id) ?? Number.POSITIVE_INFINITY;
    const bucket = buckets.get(hop) ?? [];
    bucket.push(node);
    buckets.set(hop, bucket);
  }
  let seated = 0;
  for (const [hop, bucket] of buckets) {
    bucket.forEach((node, index) => {
      const pose = orbitPose(node.id, hop, index, bucket.length);
      node.x = pose.x;
      node.y = pose.y;
      node.z = pose.z;
      seated += 1;
    });
  }
  return seated;
}
