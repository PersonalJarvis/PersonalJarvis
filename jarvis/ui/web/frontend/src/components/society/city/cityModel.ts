/** Metre-based city: rendering and routing share the same authored anchors. */
export type Vec3 = [number, number, number];
export type Stop = "west" | "east" | "knowledge" | "workshop" | "communications";
export type DistrictId = "central" | "terminal" | "knowledge" | "workshop" | "communications";
export interface RouteNode { id: string; position: Vec3; layer: "ground" | "platform" | "bridge" | "interior"; }
export interface RouteEdge { from: string; to: string; speed: number; kind: "walk" | "lift" | "interior"; }
export const CITY = { width: 1800, depth: 1200, referenceWidth: 600, referenceDepth: 400, grid: 4 };
export const CAMERA_BOUNDS = { minX: -850, maxX: 850, minZ: -550, maxZ: 550, minDistance: 5, maxDistance: 1500 };
export const MATERIALS = { concrete: "#b8b5ac", graphite: "#252c32", metal: "#aab4bc", glass: "#708b95", green: "#60714d", light: "#ffe1ad" };
/** Clockwise order, matching the train tangent and platform side. */
export const STOPS: Stop[] = ["west", "east", "knowledge", "communications", "workshop"];
export const TRAIN_FLOOR = 12.08;
export const distance = (a: Vec3, b: Vec3) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
export const mix = (a: Vec3, b: Vec3, t: number): Vec3 => a.map((v, i) => v + (b[i] - v) * t) as Vec3;
/** Three.js Y rotation; train/station local +X points along the track. */
export function transformPoint(origin: Vec3, yaw: number, p: Vec3): Vec3 {
  const c = Math.cos(yaw), s = Math.sin(yaw);
  return [origin[0] + c * p[0] + s * p[2], origin[1] + p[1], origin[2] - s * p[0] + c * p[2]];
}
export const RAIL_SEGMENTS = 320;
export function railPoint(progress: number): Vec3 {
  const angle = Math.PI * 0.7 - progress * Math.PI * 2;
  return [500 * Math.cos(angle), 12, -110 + 280 * Math.sin(angle)];
}
export function railHeading(progress: number): number {
  const angle = Math.PI * 0.7 - progress * Math.PI * 2;
  return Math.atan2(280 * Math.cos(angle), 500 * Math.sin(angle));
}
export const RAIL_POINTS: Vec3[] = Array.from({ length: RAIL_SEGMENTS + 1 }, (_, i) => railPoint(i / RAIL_SEGMENTS));
export interface District {
  id: DistrictId; stop: Stop; labelKey: string;
  placeId: "civic" | "cli" | "archive" | "skills" | "comms";
  color: string; x: number; z: number;
  stationPosition: Vec3; stationYaw: number;
  buildingPosition: Vec3; buildingRotation: number; workplaces: Vec3[];
}
const districtInfo: Record<Stop, { id: DistrictId; color: string; placeId: District["placeId"] }> = {
  west: { id: "central", color: "#d6b76a", placeId: "civic" },
  east: { id: "terminal", color: "#54c9e0", placeId: "cli" },
  knowledge: { id: "knowledge", color: "#799cde", placeId: "archive" },
  workshop: { id: "workshop", color: "#e7a365", placeId: "skills" },
  communications: { id: "communications", color: "#65c7b4", placeId: "comms" },
};
export function localWorkplaces(stop: Stop): Vec3[] {
  const rows = stop === "east" ? [-9, -5, -1, 3, 7, 11] : [-8, -3, 2, 7, 12, 16];
  return rows.flatMap((z) => [-12, -6, 0, 6, 12].map((x): Vec3 => [x, 0, z]));
}
export const DISTRICTS: District[] = STOPS.map((stop, index) => {
  const info = districtInfo[stop];
  const stationPosition = railPoint(index / STOPS.length);
  const stationYaw = railHeading(index / STOPS.length);
  const p = transformPoint([stationPosition[0], 0, stationPosition[2]], stationYaw, [0, 0, 88]);
  const buildingPosition: Vec3 = [Math.round(p[0] / 4) * 4, 0, Math.round(p[2] / 4) * 4];
  return { ...info, stop, labelKey: `society.city.${info.id}`, x: buildingPosition[0], z: buildingPosition[2], stationPosition, stationYaw, buildingPosition, buildingRotation: stationYaw, workplaces: localWorkplaces(stop).map((p) => transformPoint(buildingPosition, stationYaw, p)) };
});
export const STATIONS = DISTRICTS;
export const district = (stop: Stop): District => DISTRICTS.find((d) => d.stop === stop)!;
export const stopX = (stop: Stop): number => district(stop).stationPosition[0];
export const otherStop = (stop: Stop): Stop => STOPS[(STOPS.indexOf(stop) + 1) % STOPS.length];
export type Placements = Record<Stop, Vec3>;
export const defaultPlacements = (): Placements => Object.fromEntries(DISTRICTS.map((d) => [d.stop, [...d.buildingPosition]])) as Placements;
export interface Plot { id: string; x: number; z: number; width: number; depth: number; }
export const PLOTS: Plot[] = DISTRICTS.map((d) => ({ id: d.stop, x: d.x, z: d.z, width: 96, depth: 96 }));
export function validPlacement(plot: Plot, x: number, z: number, width: number, depth: number, occupied: Plot[]): boolean {
  if (![x, z, width, depth].every(Number.isFinite) || width <= 0 || depth <= 0 || x % CITY.grid !== 0 || z % CITY.grid !== 0) return false;
  if (Math.abs(x - plot.x) + width / 2 > plot.width / 2 || Math.abs(z - plot.z) + depth / 2 > plot.depth / 2) return false;
  if (Math.abs(x) + width / 2 > CITY.width / 2 || Math.abs(z) + depth / 2 > CITY.depth / 2) return false;
  const d = DISTRICTS.find((candidate) => candidate.stop === plot.id);
  if (!d) return false;
  const entrance = transformPoint([x, 0, z], d.buildingRotation, [0, 0, -19]);
  const plaza = transformPoint([d.stationPosition[0], 0, d.stationPosition[2]], d.stationYaw, [0, 0, 42]);
  const outward = transformPoint([0, 0, 0], d.stationYaw, [0, 0, 1]);
  // The building must remain outside its plaza, with a clear four-metre entrance strip.
  if ((entrance[0] - plaza[0]) * outward[0] + (entrance[2] - plaza[2]) * outward[2] < 8) return false;
  const minX = Math.min(entrance[0], plaza[0]) - 2, maxX = Math.max(entrance[0], plaza[0]) + 2;
  const minZ = Math.min(entrance[2], plaza[2]) - 2, maxZ = Math.max(entrance[2], plaza[2]) + 2;
  return !occupied.some((p) =>
    (Math.abs(x - p.x) < (width + p.width) / 2 && Math.abs(z - p.z) < (depth + p.depth) / 2) ||
    (p.x + p.width / 2 > minX && p.x - p.width / 2 < maxX && p.z + p.depth / 2 > minZ && p.z - p.depth / 2 < maxZ));
}
export interface SavedCityLayout { version: 2; placements: Placements; }
/** Earlier preview/island coordinates are explicitly migrated to default city plots. */
export function restoreLayout(value: unknown): SavedCityLayout {
  const placements = defaultPlacements();
  if (!value || typeof value !== "object" || !("version" in value) || value.version !== 2 || !("placements" in value) || !value.placements || typeof value.placements !== "object") return { version: 2, placements };
  for (const d of DISTRICTS) {
    const p = (value.placements as Record<string, unknown>)[d.stop];
    if (Array.isArray(p) && p.length === 3 && p.every((n) => typeof n === "number" && Number.isFinite(n)) && p[1] === 0 && validPlacement(PLOTS.find((plot) => plot.id === d.stop)!, p[0], p[2], 58, 58, [])) placements[d.stop] = [p[0], 0, p[2]];
  }
  return { version: 2, placements };
}
export interface RouteGraph {
  nodes: Map<string, RouteNode>; edges: RouteEdge[];
  outgoing: Map<string, RouteEdge[]>; cache: Map<string, string[] | null>;
}
export function createRouteGraph(placements: Placements = defaultPlacements()): RouteGraph {
  const nodes = new Map<string, RouteNode>();
  const edges: RouteEdge[] = [];
  const addNode = (id: string, position: Vec3, layer: RouteNode["layer"]) => nodes.set(id, { id, position, layer });
  const link = (from: string, to: string, speed: number, kind: RouteEdge["kind"] = "walk") => { edges.push({ from, to, speed, kind }, { from: to, to: from, speed, kind }); };
  for (const d of DISTRICTS) {
    const base: Vec3 = [d.stationPosition[0], 0, d.stationPosition[2]];
    const local = (p: Vec3) => transformPoint(base, d.stationYaw, p);
    const buildingLocal = (p: Vec3) => transformPoint(placements[d.stop], d.buildingRotation, p);
    const s = d.stop;
    addNode(`${s}:platform`, local([0, TRAIN_FLOOR, 4.2]), "platform");
    addNode(`${s}:upper`, local([0, TRAIN_FLOOR, 15]), "platform");
    addNode(`${s}:lift`, local([0, 0, 15]), "ground");
    addNode(`${s}:plaza`, local([0, 0, 42]), "ground");
    addNode(`${s}:entry`, buildingLocal([0, 0, -19]), "ground");
    addNode(`${s}:foyer`, buildingLocal([-3, 0, -15]), "interior");
    link(`${s}:platform`, `${s}:upper`, 1.8);
    link(`${s}:upper`, `${s}:lift`, 3, "lift");
    link(`${s}:lift`, `${s}:plaza`, 2.4);
    link(`${s}:plaza`, `${s}:entry`, 2.4);
    link(`${s}:entry`, `${s}:foyer`, 1.5, "interior");
    localWorkplaces(s).forEach((p, index) => {
      const row = Math.floor(index / 5), aisle = `${s}:aisle:${row}`;
      if (!nodes.has(aisle)) {
        addNode(aisle, buildingLocal([-3, 0, p[2] - 1.5]), "interior");
        link(row === 0 ? `${s}:foyer` : `${s}:aisle:${row - 1}`, aisle, 1.5, "interior");
      }
      addNode(`${s}:approach:${index}`, buildingLocal([p[0], 0, p[2] - 1.5]), "interior");
      addNode(`${s}:work:${index}`, buildingLocal(p), "interior");
      link(aisle, `${s}:approach:${index}`, 1.5, "interior");
      link(`${s}:approach:${index}`, `${s}:work:${index}`, 1.5, "interior");
    });
    addNode(`${s}:work`, buildingLocal([0, 0, -9]), "interior");
    link(`${s}:aisle:0`, `${s}:work`, 1.5, "interior");
  }
  // The public promenade follows the viaduct outside, without cutting across halls.
  for (let index = 0; index < STOPS.length; index++) {
    let previous = `${STOPS[index]}:plaza`;
    for (let step = 1; step < 16; step++) {
      const progress = (index + step / 16) / STOPS.length;
      const p = railPoint(progress), id = `promenade:${index}:${step}`;
      addNode(id, transformPoint([p[0], 0, p[2]], railHeading(progress), [0, 0, 42]), "ground");
      link(previous, id, 2.4);
      previous = id;
    }
    link(previous, `${otherStop(STOPS[index])}:plaza`, 2.4);
  }
  addNode("cross:ground", [0, 0, 200], "ground");
  addNode("cross:bridge", [0, 20, 200], "bridge");
  addNode("bridge:north", [0, 20, 80], "bridge");
  addNode("bridge:south", [0, 20, 260], "bridge");
  addNode("bridge:base", [0, 0, 260], "ground");
  link("promenade:0:8", "cross:ground", 2.4);
  link("cross:ground", "bridge:base", 2.4);
  link("bridge:base", "bridge:south", 3, "lift");
  link("bridge:south", "cross:bridge", 2.4);
  link("cross:bridge", "bridge:north", 2.4);
  const outgoing = new Map<string, RouteEdge[]>();
  for (const e of edges) outgoing.set(e.from, [...(outgoing.get(e.from) ?? []), e]);
  return { nodes, edges, outgoing, cache: new Map() };
}
export const DEFAULT_GRAPH = createRouteGraph();
export const NODES = [...DEFAULT_GRAPH.nodes.values()];
export const EDGES = DEFAULT_GRAPH.edges;
export const node = (id: string, graph: RouteGraph = DEFAULT_GRAPH): RouteNode | undefined => graph.nodes.get(id);
export interface Walkway { id: string; a: Vec3; b: Vec3; width: number; layer: RouteNode["layer"]; }
export function graphWalkways(graph: RouteGraph): Walkway[] {
  return graph.edges.filter((e) => e.from < e.to && e.kind === "walk").map((e) => ({ id: `${e.from}/${e.to}`, a: node(e.from, graph)!.position, b: node(e.to, graph)!.position, width: e.from.includes("platform") || e.to.includes("platform") ? 5 : 6, layer: node(e.from, graph)!.layer }));
}
export const WALKWAYS = graphWalkways(DEFAULT_GRAPH);
export const LIFTS = EDGES.filter((e) => e.kind === "lift" && node(e.from)!.position[1] < node(e.to)!.position[1]).map((e) => ({ id: e.from, a: node(e.from)!.position, b: node(e.to)!.position }));
/** Indexed adjacency and reusable paths avoid searching the graph on every frame. */
export function route(from: string, to: string, blocked = new Set<string>(), graph: RouteGraph = DEFAULT_GRAPH): string[] | null {
  if (!node(from, graph) || !node(to, graph) || blocked.has(to) || blocked.has(from)) return null;
  const key = `${from}|${to}`;
  if (blocked.size === 0 && graph.cache.has(key)) { const cached = graph.cache.get(key); return cached ? [...cached] : null; }
  const costs = new Map([[from, 0]]), previous = new Map<string, string>(), visited = new Set<string>();
  while (true) {
    let id: string | undefined, cost = Infinity;
    for (const [candidate, value] of costs) if (!visited.has(candidate) && value < cost) { id = candidate; cost = value; }
    if (id === undefined) { if (blocked.size === 0) graph.cache.set(key, null); return null; }
    if (id === to) {
      const path = [to];
      while (previous.has(path[0])) path.unshift(previous.get(path[0])!);
      if (blocked.size === 0) graph.cache.set(key, path);
      return [...path];
    }
    visited.add(id);
    for (const edge of graph.outgoing.get(id) ?? []) {
      if (blocked.has(edge.to)) continue;
      const next = cost + distance(node(id, graph)!.position, node(edge.to, graph)!.position) / edge.speed;
      if (next < (costs.get(edge.to) ?? Infinity)) { costs.set(edge.to, next); previous.set(edge.to, id); }
    }
  }
}
export function routeSeconds(path: string[] | null, graph: RouteGraph = DEFAULT_GRAPH): number {
  if (!path) return Infinity;
  return path.slice(1).reduce((sum, id, i) => {
    const edge = graph.outgoing.get(path[i])?.find((e) => e.to === id);
    return edge ? sum + distance(node(path[i], graph)!.position, node(id, graph)!.position) / edge.speed : Infinity;
  }, 0);
}
/** This maps presentation checkpoints; dispatch never depends on travel. */
export function checkpointStop(checkpoint: string): Stop | null {
  if (["desk", "hub:cli", "hub:desktop", "hub:models"].includes(checkpoint)) return "east";
  if (["archive", "gallery"].includes(checkpoint)) return "knowledge";
  if (["hub:skills", "hub:plugins"].includes(checkpoint)) return "workshop";
  if (["hub:mcp", "hub:web", "hub:comms", "gate"].includes(checkpoint)) return "communications";
  if (["meeting", "civic"].includes(checkpoint)) return "west";
  return null;
}
