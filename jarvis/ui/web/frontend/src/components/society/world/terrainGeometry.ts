/**
 * The island's terrain as ONE merged mesh — stepped tiles with vertex colours.
 *
 * Every land tile contributes a flat top quad at its level's height and, where
 * a neighbour sits lower (or is sea), a vertical side quad down to that
 * neighbour: cliffs, terraces, beach steps and the plateau rim come out of the
 * level map for free. Colours are per tile (two alternating shades per kind,
 * so the ground has the tile-art flicker under the pixel pass) and per side;
 * on top of that the high ground is tinted a touch lighter and the sand next
 * to the water a touch darker (wet), the way a painted map reads height and
 * shoreline.
 *
 * Pure three.js, no React: a test can build it under vitest, and the renderer
 * mounts the result once. ~36 k land tiles → roughly 110 k triangles.
 */
import { BufferAttribute, BufferGeometry, Color } from "three";

import {
  DOCK_Y,
  LEVEL_Y,
  PLATEAU_LEVEL,
  TILE_M,
  TileKind,
  hash2,
  inBounds,
  tileIndex,
  type IslandMap,
} from "./islandLayout";
import { TILE_COLORS } from "./worldPalette";

/** How far below the water surface the coast walls reach (never a visible gap). */
const SEA_FLOOR_Y = -1.5;
/** Lightening per level above the plateau (and darkening below): aerial tint. */
const LEVEL_TINT = 0.022;
/** Sand with the sea beside it darkens by this factor: the wet line. */
const WET_SAND = 0.9;

type Vec3 = [number, number, number];

class MeshBuilder {
  positions: number[] = [];
  normals: number[] = [];
  colors: number[] = [];

  /** A quad from four corners in counter-clockwise order (seen from `normal`). */
  quad(a: Vec3, b: Vec3, c: Vec3, d: Vec3, normal: Vec3, color: Color): void {
    this.tri(a, b, c, normal, color);
    this.tri(a, c, d, normal, color);
  }

  private tri(a: Vec3, b: Vec3, c: Vec3, n: Vec3, col: Color): void {
    this.positions.push(...a, ...b, ...c);
    this.normals.push(...n, ...n, ...n);
    this.colors.push(col.r, col.g, col.b, col.r, col.g, col.b, col.r, col.g, col.b);
  }

  build(): BufferGeometry {
    const geo = new BufferGeometry();
    geo.setAttribute("position", new BufferAttribute(new Float32Array(this.positions), 3));
    geo.setAttribute("normal", new BufferAttribute(new Float32Array(this.normals), 3));
    geo.setAttribute("color", new BufferAttribute(new Float32Array(this.colors), 3));
    geo.computeBoundingSphere();
    return geo;
  }
}

const UP: Vec3 = [0, 1, 0];
const SOUTH: Vec3 = [0, 0, 1];
const NORTH: Vec3 = [0, 0, -1];
const EAST: Vec3 = [1, 0, 0];
const WEST: Vec3 = [-1, 0, 0];

const colorCache = new Map<string, Color>();
function color(hex: string): Color {
  let c = colorCache.get(hex);
  if (!c) {
    c = new Color(hex);
    colorCache.set(hex, c);
  }
  return c;
}

const NEIGHBOURS = [
  [1, 0],
  [-1, 0],
  [0, 1],
  [0, -1],
] as const;

/** Height of a tile's top surface; the sea floor for water and out-of-map. */
function topY(map: IslandMap, tx: number, tz: number): number {
  if (!inBounds(map, tx, tz)) return SEA_FLOOR_Y;
  const i = tileIndex(map, tx, tz);
  const kind = map.kind[i];
  if (kind === TileKind.water) return SEA_FLOOR_Y;
  if (kind === TileKind.dock) return DOCK_Y;
  return LEVEL_Y[map.level[i]];
}

/** 1 = fully lit; each higher neighbour and each built-on neighbour darkens the tile a step. */
function tileAo(map: IslandMap, tx: number, tz: number, y: number): number {
  let ao = 1;
  for (const [dx, dz] of NEIGHBOURS) {
    const nx = tx + dx;
    const nz = tz + dz;
    if (!inBounds(map, nx, nz)) continue;
    const ny = topY(map, nx, nz);
    // A higher neighbour shades; a cliff (two steps or more) shades harder.
    if (ny > y + 0.01) ao -= ny > y + 1.2 ? 0.12 : 0.09;
    else if (map.blocked[tileIndex(map, nx, nz)] && map.kind[tileIndex(map, nx, nz)] !== TileKind.water) ao -= 0.05;
  }
  return Math.max(0.64, ao);
}

function touchesWater(map: IslandMap, tx: number, tz: number): boolean {
  for (const [dx, dz] of NEIGHBOURS) {
    const nx = tx + dx;
    const nz = tz + dz;
    if (!inBounds(map, nx, nz)) return true;
    if (map.kind[tileIndex(map, nx, nz)] === TileKind.water) return true;
  }
  return false;
}

/** Build the terrain mesh for the whole map. */
export function buildTerrainGeometry(map: IslandMap): BufferGeometry {
  const b = new MeshBuilder();
  const half = (map.size * TILE_M) / 2;
  for (let tz = 0; tz < map.size; tz++) {
    for (let tx = 0; tx < map.size; tx++) {
      const i = tileIndex(map, tx, tz);
      const kind = map.kind[i] as TileKind;
      if (kind === TileKind.water) continue;
      const y = topY(map, tx, tz);
      const level = map.level[i];
      const x0 = tx * TILE_M - half;
      const x1 = x0 + TILE_M;
      const z0 = tz * TILE_M - half;
      const z1 = z0 + TILE_M;
      const shades = TILE_COLORS[kind];
      const shade = hash2(tx, tz, 91) < 0.5 ? 0 : 1;
      // Garden beds: the second shade is the flower colour, sprinkled sparsely.
      // Farmland: the two shades alternate by row — soil and crop, ploughed lines.
      const topIdx =
        kind === TileKind.garden ? (hash2(tx, tz, 92) < 0.3 ? 1 : 0) : kind === TileKind.farm ? tz % 2 : shade;
      // Analytic ambient occlusion (world-masterplan-v2.md §3.3): ground next
      // to a higher step or a building darkens a little, the way every corner
      // of a stylised village is shaded.
      let factor = tileAo(map, tx, tz, y);
      // Aerial tint: the high ground is a touch lighter, the low ground a touch deeper.
      if (kind !== TileKind.dock && kind !== TileKind.pool) factor *= 1 + (level - PLATEAU_LEVEL) * LEVEL_TINT;
      // The wet line: sand with the sea beside it.
      if (kind === TileKind.sand && touchesWater(map, tx, tz)) factor *= WET_SAND;
      const base = color(shades.top[topIdx]);
      const top = Math.abs(factor - 1) > 0.001 ? base.clone().multiplyScalar(factor) : base;
      b.quad([x0, y, z0], [x0, y, z1], [x1, y, z1], [x1, y, z0], UP, top);

      // Dock planks stand on posts, not on a wall: no side faces, they float.
      if (kind === TileKind.dock) continue;

      const side = color(shades.side);
      const south = topY(map, tx, tz + 1);
      if (south < y) b.quad([x0, south, z1], [x1, south, z1], [x1, y, z1], [x0, y, z1], SOUTH, side);
      const north = topY(map, tx, tz - 1);
      if (north < y) b.quad([x1, north, z0], [x0, north, z0], [x0, y, z0], [x1, y, z0], NORTH, side);
      const east = topY(map, tx + 1, tz);
      if (east < y) b.quad([x1, east, z1], [x1, east, z0], [x1, y, z0], [x1, y, z1], EAST, side);
      const west = topY(map, tx - 1, tz);
      if (west < y) b.quad([x0, west, z0], [x0, west, z1], [x0, y, z1], [x0, y, z0], WEST, side);
    }
  }
  return b.build();
}

/** Triangle count of a non-indexed geometry — for tests and the dev HUD. */
export function triangleCount(geo: BufferGeometry): number {
  const pos = geo.getAttribute("position");
  return pos ? pos.count / 3 : 0;
}
