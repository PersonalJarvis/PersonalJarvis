/** Metre-based reference district. Layer identity is part of every route node. */
export type Vec3 = [number, number, number];
export type Stop = "west" | "east";
export interface RouteNode {
  id: string;
  position: Vec3;
  layer: "ground" | "platform" | "bridge";
}
export interface RouteEdge {
  from: string;
  to: string;
  speed: number;
}
export const CITY = {
  width: 1800,
  depth: 1200,
  referenceWidth: 600,
  referenceDepth: 400,
  grid: 4,
};
export const MATERIALS = {
  concrete: "#b8b5ac",
  graphite: "#252c32",
  metal: "#aab4bc",
  glass: "#708b95",
  green: "#60714d",
  light: "#ffe1ad",
};
export const DISTRICTS = [
  { id: "central", color: "#d6b76a", x: -120, z: 80 },
  { id: "terminal", color: "#54c9e0", x: 120, z: 80 },
  { id: "knowledge", color: "#799cde", x: -520, z: -260 },
  { id: "workshop", color: "#e7a365", x: 520, z: -260 },
  { id: "communications", color: "#65c7b4", x: 0, z: -440 },
] as const;
export const STOPS: Stop[] = ["west", "east"];
export const stopX = (stop: Stop) => (stop === "west" ? -120 : 120);
export const otherStop = (stop: Stop): Stop =>
  stop === "west" ? "east" : "west";
export const NODES: RouteNode[] = STOPS.flatMap((s) => [
  {
    id: `${s}:work`,
    position: [stopX(s), 0, 84] as Vec3,
    layer: "ground" as const,
  },
  {
    id: `${s}:plaza`,
    position: [stopX(s), 0, 40] as Vec3,
    layer: "ground" as const,
  },
  {
    id: `${s}:lift`,
    position: [stopX(s), 0, 16] as Vec3,
    layer: "ground" as const,
  },
  {
    id: `${s}:upper`,
    position: [stopX(s), 12, 16] as Vec3,
    layer: "platform" as const,
  },
  {
    id: `${s}:platform`,
    position: [stopX(s), 12, 8] as Vec3,
    layer: "platform" as const,
  },
]);
NODES.push(
  { id: "cross:ground", position: [0, 0, 40], layer: "ground" },
  { id: "cross:bridge", position: [0, 20, 40], layer: "bridge" },
  { id: "bridge:north", position: [0, 20, -40], layer: "bridge" },
  { id: "bridge:south", position: [0, 20, 80], layer: "bridge" },
  { id: "bridge:base", position: [0, 0, 80], layer: "ground" },
);
const links: [string, string, number][] = STOPS.flatMap(
  (s) =>
    [
      [`${s}:work`, `${s}:plaza`, 1.28],
      [`${s}:plaza`, `${s}:lift`, 1.28],
      [`${s}:lift`, `${s}:upper`, 2],
      [`${s}:upper`, `${s}:platform`, 1.28],
    ] as [string, string, number][],
);
links.push(
  ["west:plaza", "cross:ground", 1.28],
  ["cross:ground", "east:plaza", 1.28],
  ["cross:ground", "bridge:base", 1.28],
  ["bridge:base", "bridge:south", 2],
  ["bridge:south", "cross:bridge", 1.28],
  ["cross:bridge", "bridge:north", 1.28],
);
export const EDGES: RouteEdge[] = links.flatMap(([from, to, speed]) => [
  { from, to, speed },
  { from: to, to: from, speed },
]);
export const node = (id: string) => NODES.find((n) => n.id === id);
export const distance = (a: Vec3, b: Vec3) =>
  Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
export const mix = (a: Vec3, b: Vec3, t: number): Vec3 =>
  a.map((v, i) => v + (b[i] - v) * t) as Vec3;

/** Dijkstra over explicit edges: coincident X/Z never implies a floor change. */
export function route(
  from: string,
  to: string,
  blocked = new Set<string>(),
): string[] | null {
  if (!node(from) || !node(to) || blocked.has(to)) return null;
  const costs = new Map([[from, 0]]);
  const previous = new Map<string, string>();
  const visited = new Set<string>();
  while (true) {
    const current = [...costs]
      .filter(([id]) => !visited.has(id))
      .sort((a, b) => a[1] - b[1])[0];
    if (!current) return null;
    const [id, cost] = current;
    if (id === to) {
      const path = [to];
      while (previous.has(path[0])) path.unshift(previous.get(path[0])!);
      return path;
    }
    visited.add(id);
    for (const edge of EDGES.filter(
      (e) => e.from === id && !blocked.has(e.to),
    )) {
      const next =
        cost +
        distance(node(id)!.position, node(edge.to)!.position) / edge.speed;
      if (next < (costs.get(edge.to) ?? Infinity)) {
        costs.set(edge.to, next);
        previous.set(edge.to, id);
      }
    }
  }
}
export function routeSeconds(path: string[] | null): number {
  if (!path) return Infinity;
  return path
    .slice(1)
    .reduce(
      (sum, id, i) =>
        sum +
        distance(node(path[i])!.position, node(id)!.position) /
          EDGES.find((e) => e.from === path[i] && e.to === id)!.speed,
      0,
    );
}
export function checkpointStop(checkpoint: string): Stop | null {
  if (
    [
      "desk",
      "hub:cli",
      "hub:models",
      "hub:skills",
      "hub:plugins",
      "hub:desktop",
    ].includes(checkpoint)
  )
    return "east";
  if (
    [
      "meeting",
      "archive",
      "gate",
      "gallery",
      "hub:mcp",
      "hub:web",
      "hub:comms",
    ].includes(checkpoint)
  )
    return "west";
  return null;
}
export interface Plot {
  id: string;
  x: number;
  z: number;
  width: number;
  depth: number;
}
export const PLOTS: Plot[] = STOPS.map((s) => ({
  id: s,
  x: stopX(s),
  z: 104,
  width: 64,
  depth: 64,
}));
export function validPlacement(
  plot: Plot,
  x: number,
  z: number,
  width: number,
  depth: number,
  occupied: Plot[],
): boolean {
  if (
    ![x, z, width, depth].every(Number.isFinite) ||
    width <= 0 ||
    depth <= 0 ||
    x % CITY.grid ||
    z % CITY.grid
  )
    return false;
  if (
    Math.abs(x - plot.x) + width / 2 > plot.width / 2 ||
    Math.abs(z - plot.z) + depth / 2 > plot.depth / 2
  )
    return false;
  // The south-facing entrance must retain a clear access strip to the fixed plot entrance.
  if (z - depth / 2 < 72 || Math.abs(x - plot.x) > 16) return false;
  return !occupied.some(
    (p) =>
      Math.abs(x - p.x) < (width + p.width) / 2 &&
      Math.abs(z - p.z) < (depth + p.depth) / 2,
  );
}
