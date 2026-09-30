/**
 * Grid navigation for the office floor: an occupancy grid built from the
 * layout's obstacles (inflated by the walker radius), A* over it, and
 * line-of-sight smoothing so figures walk straight lines instead of
 * staircases.
 *
 * Pure and allocation-light: per-grid scratch buffers are cached, so a path
 * search on a full floor costs well under a few milliseconds.
 */
import type { OfficeLayout, Point, Rect } from "./officeLayout";

/** Half the shoulder width of a walking figure, in metres. */
export const WALKER_RADIUS = 0.28;

export interface NavGrid {
  /** Edge length of one square cell, in metres. */
  cell: number;
  cols: number;
  rows: number;
  /** World position of the grid's north-west corner. */
  originX: number;
  originZ: number;
  /** 1 = blocked, 0 = walkable; index = row * cols + col. */
  blocked: Uint8Array;
}

/** A snapped approach may end at most this far from a blocked target. */
const SNAP_DISTANCE = 1.0;
/** How far a blocked start or an unreachable target may search for a walkable cell. */
const FALLBACK_SEARCH = 3.0;
const SQRT2 = Math.SQRT2;

export function buildNavGrid(layout: OfficeLayout, opts: { cell?: number; radius?: number } = {}): NavGrid {
  const cell = opts.cell ?? 0.2;
  const radius = opts.radius ?? WALKER_RADIUS;
  const { floor } = layout;
  const cols = Math.max(1, Math.ceil((floor.maxX - floor.minX) / cell));
  const rows = Math.max(1, Math.ceil((floor.maxZ - floor.minZ) / cell));
  const originX = floor.minX;
  const originZ = floor.minZ;
  const blocked = new Uint8Array(cols * rows);

  // Cells whose centre lies outside the walkable floor.
  for (let r = 0; r < rows; r += 1) {
    const cz = originZ + (r + 0.5) * cell;
    for (let c = 0; c < cols; c += 1) {
      const cx = originX + (c + 0.5) * cell;
      if (cx < floor.minX || cx > floor.maxX || cz < floor.minZ || cz > floor.maxZ) blocked[r * cols + c] = 1;
    }
  }
  // Cells whose centre lies inside an inflated obstacle.
  for (const o of layout.obstacles) {
    const minX = o.minX - radius, maxX = o.maxX + radius, minZ = o.minZ - radius, maxZ = o.maxZ + radius;
    const c0 = Math.max(0, Math.ceil((minX - originX) / cell - 0.5));
    const c1 = Math.min(cols - 1, Math.floor((maxX - originX) / cell - 0.5));
    const r0 = Math.max(0, Math.ceil((minZ - originZ) / cell - 0.5));
    const r1 = Math.min(rows - 1, Math.floor((maxZ - originZ) / cell - 0.5));
    for (let r = r0; r <= r1; r += 1) {
      for (let c = c0; c <= c1; c += 1) blocked[r * cols + c] = 1;
    }
  }
  return { cell, cols, rows, originX, originZ, blocked };
}

function colOf(grid: NavGrid, x: number): number {
  return Math.floor((x - grid.originX) / grid.cell);
}

function rowOf(grid: NavGrid, z: number): number {
  return Math.floor((z - grid.originZ) / grid.cell);
}

function walkableCell(grid: NavGrid, c: number, r: number): boolean {
  return c >= 0 && r >= 0 && c < grid.cols && r < grid.rows && grid.blocked[r * grid.cols + c] === 0;
}

function centreOf(grid: NavGrid, index: number): Point {
  const c = index % grid.cols;
  const r = (index - c) / grid.cols;
  return { x: grid.originX + (c + 0.5) * grid.cell, z: grid.originZ + (r + 0.5) * grid.cell };
}

/** Index of the cell containing p, or -1 when p lies outside the grid. */
function indexOf(grid: NavGrid, p: Point): number {
  const c = colOf(grid, p.x);
  const r = rowOf(grid, p.z);
  if (c < 0 || r < 0 || c >= grid.cols || r >= grid.rows) return -1;
  return r * grid.cols + c;
}

export function isWalkable(grid: NavGrid, p: Point): boolean {
  return walkableCell(grid, colOf(grid, p.x), rowOf(grid, p.z));
}

/** Nearest walkable cell (by centre distance) within maxRadius that passes `accept`; -1 if none. */
function nearestCell(grid: NavGrid, p: Point, maxRadius: number, accept?: (index: number) => boolean): number {
  const pc = Math.min(grid.cols - 1, Math.max(0, colOf(grid, p.x)));
  const pr = Math.min(grid.rows - 1, Math.max(0, rowOf(grid, p.z)));
  // Distance from p to its (clamped) cell, so points just off the grid still search correctly.
  const offGrid = Math.hypot(
    Math.max(0, grid.originX - p.x, p.x - (grid.originX + grid.cols * grid.cell)),
    Math.max(0, grid.originZ - p.z, p.z - (grid.originZ + grid.rows * grid.cell)),
  );
  if (offGrid > maxRadius) return -1;
  const maxRing = Math.ceil((maxRadius + offGrid) / grid.cell) + 1;
  let best = -1;
  let bestDist = Infinity;
  for (let ring = 0; ring <= maxRing; ring += 1) {
    // Every centre in this ring is at least this far from p.
    if (best >= 0 && (ring - 1) * grid.cell - offGrid > bestDist) break;
    for (let dr = -ring; dr <= ring; dr += 1) {
      const r = pr + dr;
      if (r < 0 || r >= grid.rows) continue;
      const edgeRow = dr === -ring || dr === ring;
      for (let dc = -ring; dc <= ring; dc += edgeRow ? 1 : ring * 2) {
        const c = pc + dc;
        if (c >= 0 && c < grid.cols) {
          const index = r * grid.cols + c;
          if (grid.blocked[index] === 0) {
            const cx = grid.originX + (c + 0.5) * grid.cell;
            const cz = grid.originZ + (r + 0.5) * grid.cell;
            const d = Math.hypot(cx - p.x, cz - p.z);
            if (d <= maxRadius && d < bestDist && (!accept || accept(index))) { best = index; bestDist = d; }
          }
        }
        if (ring === 0) break;
      }
    }
  }
  return best;
}

/** Centre of the walkable cell nearest to p (p's own cell when walkable), or null within maxRadius. */
export function nearestWalkable(grid: NavGrid, p: Point, maxRadius = FALLBACK_SEARCH): Point | null {
  if (isWalkable(grid, p)) return { x: p.x, z: p.z };
  const index = nearestCell(grid, p, maxRadius);
  return index < 0 ? null : centreOf(grid, index);
}

/**
 * True when every cell the segment a→b touches is walkable (supercover walk).
 * A segment through a cell corner must have both side cells free, which
 * matches the "no corner cutting" rule of the path search.
 */
export function hasLineOfSight(grid: NavGrid, a: Point, b: Point): boolean {
  const ax = (a.x - grid.originX) / grid.cell, az = (a.z - grid.originZ) / grid.cell;
  const bx = (b.x - grid.originX) / grid.cell, bz = (b.z - grid.originZ) / grid.cell;
  let c = Math.floor(ax), r = Math.floor(az);
  if (!walkableCell(grid, c, r)) return false;
  const dx = bx - ax, dz = bz - az;
  const stepX = dx > 0 ? 1 : dx < 0 ? -1 : 0;
  const stepZ = dz > 0 ? 1 : dz < 0 ? -1 : 0;
  const tDeltaX = stepX !== 0 ? 1 / Math.abs(dx) : Infinity;
  const tDeltaZ = stepZ !== 0 ? 1 / Math.abs(dz) : Infinity;
  let tMaxX = stepX > 0 ? (c + 1 - ax) * tDeltaX : stepX < 0 ? (ax - c) * tDeltaX : Infinity;
  let tMaxZ = stepZ > 0 ? (r + 1 - az) * tDeltaZ : stepZ < 0 ? (az - r) * tDeltaZ : Infinity;
  const limit = Math.abs(Math.floor(bx) - c) + Math.abs(Math.floor(bz) - r) + 4;
  for (let i = 0; i < limit * 2; i += 1) {
    const next = Math.min(tMaxX, tMaxZ);
    if (next > 1) return true;
    if (Math.abs(tMaxX - tMaxZ) < 1e-9) {
      // Exactly through a corner: both side cells must be free.
      if (!walkableCell(grid, c + stepX, r) || !walkableCell(grid, c, r + stepZ)) return false;
      c += stepX; r += stepZ; tMaxX += tDeltaX; tMaxZ += tDeltaZ;
    } else if (tMaxX < tMaxZ) {
      c += stepX; tMaxX += tDeltaX;
    } else {
      r += stepZ; tMaxZ += tDeltaZ;
    }
    if (!walkableCell(grid, c, r)) return false;
  }
  return true;
}

interface Scratch {
  g: Float64Array;
  parent: Int32Array;
  seen: Uint32Array;
  closed: Uint32Array;
  stamp: number;
  heapIndex: Int32Array;
  heapKey: Float64Array;
  /** Connected-region label per cell (-1 = blocked). */
  region: Int32Array | null;
}

const scratchCache = new WeakMap<NavGrid, Scratch>();

function scratchOf(grid: NavGrid): Scratch {
  let s = scratchCache.get(grid);
  if (!s) {
    const n = grid.cols * grid.rows;
    s = {
      g: new Float64Array(n), parent: new Int32Array(n), seen: new Uint32Array(n), closed: new Uint32Array(n), stamp: 0,
      heapIndex: new Int32Array(1024), heapKey: new Float64Array(1024), region: null,
    };
    scratchCache.set(grid, s);
  }
  return s;
}

/** Label 4-connected walkable regions (equivalent to 8-connected without corner cutting). */
function regionsOf(grid: NavGrid): Int32Array {
  const s = scratchOf(grid);
  if (s.region) return s.region;
  const n = grid.cols * grid.rows;
  const region = new Int32Array(n).fill(-1);
  const queue = new Int32Array(n);
  let label = 0;
  for (let start = 0; start < n; start += 1) {
    if (grid.blocked[start] !== 0 || region[start] !== -1) continue;
    let head = 0, tail = 0;
    queue[tail++] = start;
    region[start] = label;
    while (head < tail) {
      const i = queue[head++];
      const c = i % grid.cols;
      const neighbours = [c > 0 ? i - 1 : -1, c < grid.cols - 1 ? i + 1 : -1, i - grid.cols, i + grid.cols];
      for (const j of neighbours) {
        if (j < 0 || j >= n || grid.blocked[j] !== 0 || region[j] !== -1) continue;
        region[j] = label;
        queue[tail++] = j;
      }
    }
    label += 1;
  }
  s.region = region;
  return region;
}

/** A* from cell s to cell t (8-connected, no corner cutting). Returns cells from s to t inclusive, or null. */
function aStar(grid: NavGrid, s: number, t: number): number[] | null {
  const sc = scratchOf(grid);
  sc.stamp += 1;
  if (sc.stamp === 0xffffffff) { sc.seen.fill(0); sc.closed.fill(0); sc.stamp = 1; }
  const stamp = sc.stamp;
  const { g, parent, seen, closed } = sc;
  const cols = grid.cols, rows = grid.rows, blocked = grid.blocked;
  const tc = t % cols, tr = (t - tc) / cols;
  const h = (i: number): number => {
    const c = i % cols, r = (i - c) / cols;
    const dx = Math.abs(c - tc), dz = Math.abs(r - tr);
    return dx > dz ? dx + (SQRT2 - 1) * dz : dz + (SQRT2 - 1) * dx;
  };

  // Binary min-heap with lazy deletion.
  let size = 0;
  const push = (index: number, key: number) => {
    if (size >= sc.heapIndex.length) {
      const grownI = new Int32Array(sc.heapIndex.length * 2); grownI.set(sc.heapIndex); sc.heapIndex = grownI;
      const grownK = new Float64Array(sc.heapKey.length * 2); grownK.set(sc.heapKey); sc.heapKey = grownK;
    }
    const hi = sc.heapIndex, hk = sc.heapKey;
    let pos = size++;
    while (pos > 0) {
      const up = (pos - 1) >> 1;
      if (hk[up] <= key) break;
      hi[pos] = hi[up]; hk[pos] = hk[up]; pos = up;
    }
    hi[pos] = index; hk[pos] = key;
  };
  const pop = (): number => {
    const hi = sc.heapIndex, hk = sc.heapKey;
    const top = hi[0];
    size -= 1;
    if (size > 0) {
      const lastI = hi[size], lastK = hk[size];
      let pos = 0;
      for (;;) {
        let child = pos * 2 + 1;
        if (child >= size) break;
        if (child + 1 < size && hk[child + 1] < hk[child]) child += 1;
        if (hk[child] >= lastK) break;
        hi[pos] = hi[child]; hk[pos] = hk[child]; pos = child;
      }
      hi[pos] = lastI; hk[pos] = lastK;
    }
    return top;
  };

  g[s] = 0; parent[s] = -1; seen[s] = stamp;
  push(s, h(s));
  while (size > 0) {
    const cur = pop();
    if (closed[cur] === stamp) continue;
    closed[cur] = stamp;
    if (cur === t) {
      const cells: number[] = [];
      for (let i = t; i !== -1; i = parent[i]) cells.push(i);
      return cells.reverse();
    }
    const c = cur % cols, r = (cur - c) / cols;
    for (let dr = -1; dr <= 1; dr += 1) {
      const nr = r + dr;
      if (nr < 0 || nr >= rows) continue;
      for (let dc = -1; dc <= 1; dc += 1) {
        if (dc === 0 && dr === 0) continue;
        const nc = c + dc;
        if (nc < 0 || nc >= cols) continue;
        const ni = nr * cols + nc;
        if (blocked[ni] !== 0 || closed[ni] === stamp) continue;
        let cost = 1;
        if (dc !== 0 && dr !== 0) {
          // No corner cutting: both orthogonal neighbours must be free.
          if (blocked[r * cols + nc] !== 0 || blocked[nr * cols + c] !== 0) continue;
          cost = SQRT2;
        }
        const ng = g[cur] + cost;
        if (seen[ni] === stamp && ng >= g[ni]) continue;
        seen[ni] = stamp; g[ni] = ng; parent[ni] = cur;
        push(ni, ng + h(ni));
      }
    }
  }
  return null;
}

/**
 * Waypoints from `from` to `to`, excluding the start, or null when `to` is
 * genuinely unreachable.
 *
 * A blocked start first steps to its nearest walkable cell. A blocked target
 * (a couch seat, a chair tucked against a desk) is approached through the
 * nearest reachable cell; when that cell lies within 1 m, the exact target is
 * appended as a final "snap" step that may cross the inflated margin.
 */
export function findPath(grid: NavGrid, from: Point, to: Point): Point[] | null {
  const region = regionsOf(grid);
  let s = indexOf(grid, from);
  let startSnapped = false;
  if (s < 0 || grid.blocked[s] !== 0) {
    s = nearestCell(grid, from, FALLBACK_SEARCH);
    if (s < 0) return null;
    startSnapped = true;
  }
  const home = region[s];
  const sameRegion = (index: number) => region[index] === home;

  let t = indexOf(grid, to);
  let goalSnap = false;
  const targetWalkable = t >= 0 && grid.blocked[t] === 0;
  if (targetWalkable) {
    if (region[t] !== home) return null;
  } else {
    t = nearestCell(grid, to, SNAP_DISTANCE, sameRegion);
    if (t >= 0) goalSnap = true;
    else t = nearestCell(grid, to, FALLBACK_SEARCH, sameRegion);
    if (t < 0) return null;
  }

  const cells = aStar(grid, s, t);
  if (!cells) return null;

  const startPoint = startSnapped ? centreOf(grid, s) : from;
  const points = cells.slice(1).map((i) => centreOf(grid, i));
  if (targetWalkable) {
    if (points.length > 0) points[points.length - 1] = { x: to.x, z: to.z };
    else if (Math.hypot(to.x - startPoint.x, to.z - startPoint.z) > 1e-6) points.push({ x: to.x, z: to.z });
  }

  // String pulling: drop every waypoint the previous kept point can see past.
  const smoothed: Point[] = [];
  let anchor = startPoint;
  for (let i = 0; i < points.length; i += 1) {
    if (i === points.length - 1 || !hasLineOfSight(grid, anchor, points[i + 1])) {
      smoothed.push(points[i]);
      anchor = points[i];
    }
  }

  const out: Point[] = [];
  if (startSnapped) out.push(startPoint);
  out.push(...smoothed);
  if (goalSnap) out.push({ x: to.x, z: to.z });
  return out;
}

/** A uniformly drawn walkable cell centre (inside `within` when given), or null after a bounded number of tries. */
export function randomWalkablePoint(grid: NavGrid, rng: () => number, within?: Rect): Point | null {
  const minX = Math.max(grid.originX, within?.minX ?? -Infinity);
  const maxX = Math.min(grid.originX + grid.cols * grid.cell, within?.maxX ?? Infinity);
  const minZ = Math.max(grid.originZ, within?.minZ ?? -Infinity);
  const maxZ = Math.min(grid.originZ + grid.rows * grid.cell, within?.maxZ ?? Infinity);
  if (maxX <= minX || maxZ <= minZ) return null;
  for (let attempt = 0; attempt < 64; attempt += 1) {
    const p = { x: minX + rng() * (maxX - minX), z: minZ + rng() * (maxZ - minZ) };
    const index = indexOf(grid, p);
    if (index < 0 || grid.blocked[index] !== 0) continue;
    const centre = centreOf(grid, index);
    if (centre.x >= minX && centre.x <= maxX && centre.z >= minZ && centre.z <= maxZ) return centre;
  }
  return null;
}
