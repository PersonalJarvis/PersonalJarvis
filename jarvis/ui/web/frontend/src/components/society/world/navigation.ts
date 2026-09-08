/** One collision model for walking, entrances, exits and building placement. */
import { BUILDING_ASSETS, WORLD_MANIFEST } from "./worldManifest";
import { ObstacleIndex, avoidsAgents, polygonsOverlap, rectangle, transformPolygon, type Disc, type Obstacle, type Point } from "./spatial";
import { CENTER_TILE, TILE_M, TileKind, buildIsland, findPath, groundY, houseId, nearestWalkable, tileToWorld, worldToTile, type BuildingId, type Island, type IslandMap } from "./islandLayout";

export const NAV_CELL_M = WORLD_MANIFEST.navigation.cellM;
export const FIXED_STEP_S = 1 / WORLD_MANIFEST.navigation.tickHz;
export const MAX_STEP_M = WORLD_MANIFEST.navigation.maxStepM;

export function buildingObstacles(island: Island): Obstacle[] {
  return [
    ...island.content.houses.map(h => ({ id: houseId(h.slot), polygon: rectangle(h.x, h.z, h.w * TILE_M, h.d * TILE_M, h.rotation) })),
    ...Object.entries(island.content.kitPoses).map(([id, p]) => ({
      id: `kit:${id}`, polygon: transformPolygon(BUILDING_ASSETS[id as keyof typeof BUILDING_ASSETS].collision, p.x, p.z, p.rotation),
    })),
  ];
}

/** A binary priority queue avoids an O(n) scan for each A* expansion. */
class Frontier {
  items: Array<[number, number]> = [];
  push(id: number, score: number) {
    const a = this.items; a.push([id, score]); let i = a.length - 1;
    while (i > 0) { const p = (i - 1) >> 1; if (a[p][1] <= score) break; [a[p], a[i]] = [a[i], a[p]]; i = p; }
  }
  pop(): number {
    const a = this.items, first = a[0][0], last = a.pop()!;
    if (a.length) { a[0] = last; let i = 0; for (;;) {
      let k = i * 2 + 1; if (k >= a.length) break;
      if (k + 1 < a.length && a[k + 1][1] < a[k][1]) k++;
      if (a[i][1] <= a[k][1]) break; [a[i], a[k]] = [a[k], a[i]]; i = k;
    } }
    return first;
  }
}

export class Navigation {
  readonly index: ObstacleIndex;
  readonly side: number;
  private offset = CENTER_TILE * TILE_M;
  private freeCache = new Map<number, Uint8Array>();
  constructor(readonly map: IslandMap, obstacles: readonly Obstacle[]) {
    const staticObstacles: Obstacle[] = [];
    // Merge horizontal blocked runs, including water, into exact swept
    // rectangles. Point sampling alone can miss a grazing corner collision.
    for (let z = 0; z < map.size; z++) {
      for (let x = 0; x < map.size;) {
        if (!map.blockedStatic[z * map.size + x]) { x++; continue; }
        const start = x;
        while (x < map.size && map.blockedStatic[z * map.size + x]) x++;
        const [left, cz] = tileToWorld(start, z), [right] = tileToWorld(x - 1, z);
        staticObstacles.push({ id: `ground:${start}:${z}`, polygon: rectangle((left + right) / 2, cz, (x - start) * TILE_M, TILE_M) });
      }
    }
    this.index = new ObstacleIndex([...obstacles, ...staticObstacles], WORLD_MANIFEST.navigation.sectorM);
    this.side = Math.round(map.size * TILE_M / NAV_CELL_M);
  }
  private key(p: Point): number {
    const x = Math.floor((p[0] + this.offset) / NAV_CELL_M), z = Math.floor((p[1] + this.offset) / NAV_CELL_M);
    return x < 0 || z < 0 || x >= this.side || z >= this.side ? -1 : z * this.side + x;
  }
  private point(key: number): [number, number] {
    return [(key % this.side + .5) * NAV_CELL_M - this.offset, (Math.floor(key / this.side) + .5) * NAV_CELL_M - this.offset];
  }
  private paved(p: Point): boolean {
    const [x, z] = worldToTile(...p), k = this.map.kind[z * this.map.size + x];
    return k === TileKind.path || k === TileKind.plaza || k === TileKind.dock;
  }
  free(p: Point, radius: number): boolean {
    if (!Number.isFinite(radius) || radius < 0 || !p.every(Number.isFinite) || this.key(p) < 0) return false;
    if (this.key([p[0] - radius, p[1] - radius]) < 0 || this.key([p[0] + radius, p[1] + radius]) < 0) return false;
    return this.index.clear(p, p, radius);
  }
  segment(a: Point, b: Point, radius: number): boolean {
    if (!this.free(a, radius) || !this.free(b, radius) || !this.index.clear(a, b, radius)) return false;
    const steps = Math.max(1, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / .125));
    let previous = a;
    for (let i = 0; i <= steps; i++) {
      const p: Point = [a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps];
      const dy = Math.abs(groundY(this.map, ...p) - groundY(this.map, ...previous));
      if (dy > MAX_STEP_M && !(this.paved(previous) && this.paved(p))) return false;
      previous = p;
    }
    return true;
  }
  nearest(p: Point, radius: number, occupied: Iterable<Disc> = [], id = "", reach = 12): [number, number] | null {
    const agents = Array.from(occupied);
    const available = (q: Point) => this.free(q, radius) && agents.every(a => a.id === id || Math.hypot(q[0] - a.x, q[1] - a.z) >= radius + a.radius + .1);
    if (available(p)) return [...p];
    for (let r = NAV_CELL_M; r <= reach; r += NAV_CELL_M) {
      const n = Math.ceil(Math.PI * 2 * r / NAV_CELL_M);
      for (let i = 0; i < n; i++) {
        const q: Point = [p[0] + Math.sin(i * Math.PI * 2 / n) * r, p[1] + Math.cos(i * Math.PI * 2 / n) * r];
        if (available(q)) return [...q];
      }
    }
    return null;
  }
  route(from: Point, to: Point, radius: number, occupied: Iterable<Disc> = [], id = "", corridorPadding = 3): Array<[number, number]> | null {
    const agents = Array.from(occupied).filter(a => a.id !== id);
    if (!this.free(from, radius) || !this.free(to, radius)) return null;
    const clear = (a: Point, b: Point) => this.segment(a, b, radius) && avoidsAgents(id, a, b, radius, agents);
    if (clear(from, to)) return [[...to]];
    const start = this.key(from), goal = this.key(to);
    if (start < 0 || goal < 0) return null;
    // Plan a broad corridor on the existing 2 m terrain graph first; detailed
    // motion still uses 0.5 m cells and exact swept body geometry. This keeps a
    // mountain trip from exploring the entire million-cell fine grid.
    const ca = nearestWalkable(this.map, ...worldToTile(...from));
    const cb = nearestWalkable(this.map, ...worldToTile(...to));
    const guide = ca && cb ? findPath(this.map, ca, cb) : null;
    const corridor = guide && corridorPadding > 0 ? new Set<number>() : null;
    if (corridor && guide) for (const [x, z] of [...guide, worldToTile(...from), worldToTile(...to)]) {
      for (let dz = -corridorPadding; dz <= corridorPadding; dz++) for (let dx = -corridorPadding; dx <= corridorPadding; dx++) {
        if (x + dx >= 0 && z + dz >= 0 && x + dx < this.map.size && z + dz < this.map.size) corridor.add((z + dz) * this.map.size + x + dx);
      }
    }
    // Cache only exact, radius-keyed grid centres; actual world endpoints are
    // separately swept, so sub-cell positions never inherit a false clearance.
    let cache = this.freeCache.get(radius);
    if (!cache) {
      if (this.freeCache.size >= 8) this.freeCache.delete(this.freeCache.keys().next().value!);
      cache = new Uint8Array(this.side * this.side); this.freeCache.set(radius, cache);
    }
    const g = new Map<number, number>([[start, 0]]), parent = new Map<number, number>(), closed = new Set<number>();
    const heap = new Frontier(); heap.push(start, 0);
    let count = 0;
    while (heap.items.length && count++ < 160_000) {
      const key = heap.pop(); if (closed.has(key)) continue;
      if (key === goal) {
        const path: Array<[number, number]> = [[...to]];
        let k = key;
        while (k !== start) { path.push(this.point(k)); k = parent.get(k)!; }
        path.push([...from]); path.reverse();
        if (!clear(path[path.length - 2], to)) return null;
        const result: Array<[number, number]> = [];
        for (let anchor = 0; anchor < path.length - 1;) {
          let far = Math.min(anchor + 24, path.length - 1);
          while (far > anchor + 1 && !clear(path[anchor], path[far])) far--;
          if (!clear(path[anchor], path[far])) return null;
          result.push(path[far]); anchor = far;
        }
        return result;
      }
      closed.add(key);
      const x = key % this.side, z = Math.floor(key / this.side), here = key === start ? from : this.point(key);
      for (let dz = -1; dz <= 1; dz++) for (let dx = -1; dx <= 1; dx++) {
        const nx = x + dx, nz = z + dz;
        if ((!dx && !dz) || nx < 0 || nz < 0 || nx >= this.side || nz >= this.side) continue;
        const next = nz * this.side + nx; if (closed.has(next)) continue;
        const p = this.point(next);
        if (corridor) { const [cx, cz] = worldToTile(...p); if (!corridor.has(cz * this.map.size + cx)) continue; }
        if (!cache[next]) cache[next] = this.free(p, radius) ? 1 : 2;
        if (cache[next] === 2 || !clear(here, p)) continue;
        const cost = g.get(key)! + Math.hypot(dx, dz);
        if (cost >= (g.get(next) ?? Infinity)) continue;
        parent.set(next, key); g.set(next, cost);
        heap.push(next, cost + Math.hypot(p[0] - to[0], p[1] - to[1]) / NAV_CELL_M);
      }
    }
    return corridor ? this.route(from, to, radius, agents, id, corridorPadding === 3 ? 12 : 0) : null;
  }
}

let cached: { island: Island; generation: number; navigation: Navigation } | null = null;
let generation = 0;
export function invalidateNavigation(): void { generation++; cached = null; }
export function worldNavigation(): Navigation {
  const island = buildIsland();
  if (!cached || cached.island !== island || cached.generation !== generation) {
    cached = { island, generation, navigation: new Navigation(island.map, buildingObstacles(island)) };
  }
  return cached.navigation;
}

/** No placement may cover another building, a body or an existing entrance. */
export function validBuildingTurn(island: Island, id: BuildingId, yaw: number, agents: Iterable<Disc>): boolean {
  const all = buildingObstacles(island), original = all.find(o => o.id === id);
  if (!original || !Number.isFinite(yaw)) return false;
  let polygon;
  if (id.startsWith("house:")) {
    const h = island.content.houses.find(h => houseId(h.slot) === id)!;
    polygon = rectangle(h.x, h.z, h.w * TILE_M, h.d * TILE_M, yaw);
  } else {
    const key = id.slice(4) as keyof typeof island.content.kitPoses, p = island.content.kitPoses[key];
    polygon = transformPolygon(BUILDING_ASSETS[key].collision, p.x, p.z, yaw);
  }
  if (all.some(o => o.id !== id && polygonsOverlap(polygon, o.polygon))) return false;
  const probe = new ObstacleIndex([{ id, polygon }]);
  if (Array.from(agents).some(a => !probe.clear([a.x, a.z], [a.x, a.z], a.radius))) return false;
  for (const place of Object.values(island.content.places)) {
    if (`kit:${place.id}` === id) continue;
    const p = tileToWorld(...place.standTile);
    if (!probe.clear(p, p, .8)) return false;
  }
  // Rotating into static scenery, a cliff or water is also forbidden.
  for (const point of polygon) {
    const [x, z] = worldToTile(...point);
    if (x < 0 || z < 0 || x >= island.map.size || z >= island.map.size || island.map.blockedStatic[z * island.map.size + x]) return false;
  }
  return true;
}
