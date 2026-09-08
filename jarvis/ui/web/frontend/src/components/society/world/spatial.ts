/** Pure swept-disc geometry, shared by placement and every movement mode. */
export type Point = readonly [number, number];
export type Polygon = readonly Point[];
export interface Obstacle { id: string; polygon: Polygon }
export interface Disc { id: string; x: number; z: number; radius: number }

export function pointSegmentDistance(p: Point, a: Point, b: Point): number {
  const dx = b[0] - a[0], dz = b[1] - a[1];
  const length2 = dx * dx + dz * dz;
  const t = length2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / length2)) : 0;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dz);
}

function cross(a: Point, b: Point, p: Point): number {
  return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]);
}

export function segmentsIntersect(a: Point, b: Point, c: Point, d: Point): boolean {
  if (Math.max(a[0], b[0]) < Math.min(c[0], d[0]) || Math.max(c[0], d[0]) < Math.min(a[0], b[0]) ||
      Math.max(a[1], b[1]) < Math.min(c[1], d[1]) || Math.max(c[1], d[1]) < Math.min(a[1], b[1])) return false;
  return cross(a, b, c) * cross(a, b, d) <= 0 && cross(c, d, a) * cross(c, d, b) <= 0;
}

export function contains(polygon: Polygon, p: Point): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i], b = polygon[j];
    if (pointSegmentDistance(p, a, b) < 1e-8) return true;
    if ((a[1] > p[1]) !== (b[1] > p[1]) && p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
}

export function sweptDiscHits(a: Point, b: Point, radius: number, polygon: Polygon): boolean {
  if (contains(polygon, a) || contains(polygon, b)) return true;
  for (let i = 0; i < polygon.length; i++) {
    const c = polygon[i], d = polygon[(i + 1) % polygon.length];
    if (segmentsIntersect(a, b, c, d) ||
        Math.min(pointSegmentDistance(a, c, d), pointSegmentDistance(b, c, d),
          pointSegmentDistance(c, a, b), pointSegmentDistance(d, a, b)) < radius - 1e-7) return true;
  }
  return false;
}

export function transformPolygon(polygon: Polygon, x: number, z: number, yaw: number): Polygon {
  const c = Math.cos(yaw), s = Math.sin(yaw);
  return polygon.map(([px, pz]) => [x + px * c + pz * s, z - px * s + pz * c]);
}

export function rectangle(x: number, z: number, width: number, depth: number, yaw = 0): Polygon {
  return transformPolygon([[-width / 2, -depth / 2], [width / 2, -depth / 2], [width / 2, depth / 2], [-width / 2, depth / 2]], x, z, yaw);
}

export function polygonsOverlap(a: Polygon, b: Polygon): boolean {
  return a.some(p => contains(b, p)) || b.some(p => contains(a, p)) ||
    a.some((p, i) => b.some((q, j) => segmentsIntersect(p, a[(i + 1) % a.length], q, b[(j + 1) % b.length])));
}

/** Broad-phase buckets; a sector is 32 m, independent of the terrain tiles. */
export class ObstacleIndex {
  private buckets = new Map<string, Obstacle[]>();
  constructor(readonly obstacles: readonly Obstacle[], readonly sectorM = 32) {
    for (const obstacle of obstacles) {
      const xs = obstacle.polygon.map(p => p[0]), zs = obstacle.polygon.map(p => p[1]);
      for (let x = Math.floor(Math.min(...xs) / sectorM); x <= Math.floor(Math.max(...xs) / sectorM); x++) {
        for (let z = Math.floor(Math.min(...zs) / sectorM); z <= Math.floor(Math.max(...zs) / sectorM); z++) {
          const key = `${x},${z}`;
          const list = this.buckets.get(key) ?? [];
          list.push(obstacle); this.buckets.set(key, list);
        }
      }
    }
  }
  query(a: Point, b: Point, radius: number): Set<Obstacle> {
    const out = new Set<Obstacle>();
    for (let x = Math.floor((Math.min(a[0], b[0]) - radius) / this.sectorM); x <= Math.floor((Math.max(a[0], b[0]) + radius) / this.sectorM); x++) {
      for (let z = Math.floor((Math.min(a[1], b[1]) - radius) / this.sectorM); z <= Math.floor((Math.max(a[1], b[1]) + radius) / this.sectorM); z++) {
        for (const obstacle of this.buckets.get(`${x},${z}`) ?? []) out.add(obstacle);
      }
    }
    return out;
  }
  clear(a: Point, b: Point, radius: number): boolean {
    for (const o of this.query(a, b, radius)) if (sweptDiscHits(a, b, radius, o.polygon)) return false;
    return true;
  }
}

export function avoidsAgents(id: string, a: Point, b: Point, radius: number, agents: Iterable<Disc>): boolean {
  for (const other of agents) {
    if (other.id === id) continue;
    const separation = radius + other.radius + 0.06;
    const point: Point = [other.x, other.z];
    if (pointSegmentDistance(point, a, b) < separation) {
      // An old save can start overlapped. Permit only motion that separates it.
      const before = Math.hypot(a[0] - other.x, a[1] - other.z);
      const after = Math.hypot(b[0] - other.x, b[1] - other.z);
      if (!(before < separation && after > before + 1e-7)) return false;
    }
  }
  return true;
}
