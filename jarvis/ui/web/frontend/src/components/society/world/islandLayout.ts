/**
 * The island as numbers — the pure model behind `components/society/world/`.
 *
 * Everything the renderer draws and the walkers walk on is derived here from a
 * fixed seed, so two windows (and a unit test) always see the SAME island.
 * Nothing in this file touches three.js or React; `islandLayout.test.ts` pins
 * the geometry the way `deckRoom.test.ts` pins the mission deck.
 *
 * Layout decisions (maintainer, 2026-09-01 — recorded in
 * docs/agent-society/world-art-direction.md):
 *
 *  - The island is a 10 × 10 grid of "fields". One field is what the viewer
 *    sees at the closest zoom without scrolling. Fields are the navigation
 *    unit, not a visible grid.
 *  - The central 4 × 4 fields are the MARKET DISTRICT: a village laid out like
 *    a ring — houses around one open square, the way a classic comic village
 *    stands around its meeting place — but built in a bright solarpunk
 *    language. The lead agent's hub stands at the head of the square.
 *  - Four quarters around the market carry the places the master plan names
 *    (§4.1): the workshop (west), the archive (north), the harbor gate on the
 *    southern bay, the lighthouse on the eastern cape; gardens and a solar
 *    field fill the corners.
 *
 * Units: 1 world unit = 1 m (matches `lib/deckRoom.ts`). One tile is 2 m.
 * World origin is the island's centre; +x is east, +z is south, so the tile
 * with index (tx, tz) has its centre at ((tx + 0.5 − 80) · 2, (tz + 0.5 − 80) · 2).
 */

// ---------------------------------------------------------------------------
// Grid constants
// ---------------------------------------------------------------------------

/** Edge of one terrain tile in metres. */
export const TILE_M = 2;
/** Tiles along one edge of a "field" — the closest zoom shows about one field. */
export const FIELD_TILES = 16;
/** Fields along one island edge. */
export const ISLAND_FIELDS = 10;
/** Tiles along one island edge (160). */
export const ISLAND_TILES = FIELD_TILES * ISLAND_FIELDS;
/** Half the island edge in metres — the pan clamp. */
export const ISLAND_HALF_M = (ISLAND_TILES * TILE_M) / 2;
/** Fields along one edge of the central market district. */
export const MARKET_FIELDS = 4;
/** Tiles along one edge of the market district (64). */
export const MARKET_TILES = MARKET_FIELDS * FIELD_TILES;
/** Half the market edge in tiles, from the centre. */
export const MARKET_HALF_TILES = MARKET_TILES / 2;
/** The island centre in tile units — also the market's centre. */
export const CENTER_TILE = ISLAND_TILES / 2;

/** Fixed seed: the island is designed, not rolled. */
export const ISLAND_SEED = 20260901;

// ---------------------------------------------------------------------------
// Tiles
// ---------------------------------------------------------------------------

export const TileKind = {
  water: 0,
  sand: 1,
  grass: 2,
  meadow: 3,
  rock: 4,
  plaza: 5,
  path: 6,
  garden: 7,
  dock: 8,
} as const;
export type TileKind = (typeof TileKind)[keyof typeof TileKind];

/**
 * Ground height per terrain level, in metres. Level 0 is the water surface;
 * the steps are small enough that a walker crossing one reads as a step, not
 * a jump, and large enough that the cliff faces show under the pixel pass.
 */
export const LEVEL_Y: readonly number[] = [0, 0.35, 1.0, 1.7, 2.6];
/** The level every road and plot sits on — the walkable plateau. */
export const PLATEAU_LEVEL = 2;
/** Height of the wooden dock above the water. */
export const DOCK_Y = 0.55;

export interface IslandMap {
  /** Tiles along one edge. */
  size: number;
  /** `TileKind` per tile, row-major (index = tz * size + tx). */
  kind: Uint8Array;
  /** Terrain level per tile (index into `LEVEL_Y`). */
  level: Uint8Array;
  /** 1 where a walker may not stand: water, rock, buildings, tree trunks. */
  blocked: Uint8Array;
}

// ---------------------------------------------------------------------------
// Places, houses, trees — the designed content
// ---------------------------------------------------------------------------

export type PlaceId =
  | "market"
  | "hub"
  | "workshop"
  | "archive"
  | "harbor"
  | "lighthouse"
  | "gardens"
  | "solar";

export interface Place {
  id: PlaceId;
  /** Tile the place is anchored on. */
  tile: [number, number];
  /** Tile a walker stands on when "at" this place (always walkable). */
  standTile: [number, number];
  /** Heading (rad, y-axis) a walker turns to once it has arrived. */
  facing: number;
}

export type HouseVariant = "solar-barrel" | "garden-roof" | "glass-loft";

export interface HousePlot {
  /** Centre of the footprint, world metres. */
  x: number;
  z: number;
  /** Footprint in tiles (w along the house's local x, d along local z). */
  w: number;
  d: number;
  /** Rotation about y; the door faces local +z. */
  rotation: number;
  variant: HouseVariant;
  /** Deterministic 0..1 for per-house variation. */
  seed: number;
}

export interface TreeSpot {
  x: number;
  z: number;
  /** Ground height under the trunk. */
  y: number;
  /** 0..1 — trunk height and canopy radius scale with it. */
  size: number;
  /** 0..1 — picks between the two canopy greens. */
  shade: number;
}

export interface Post {
  x: number;
  z: number;
  y: number;
  /** Rotation about y so a hedge segment follows the ring. */
  rotation: number;
}

export interface IslandContent {
  places: Record<PlaceId, Place>;
  houses: HousePlot[];
  trees: TreeSpot[];
  /** Hedge segments forming the village ring, with gaps at the four gates. */
  hedges: Post[];
  /** Light posts along the ring and the spokes. */
  lamps: Post[];
  /** Solar panel rows in the north-west field (centre + rotation). */
  panels: Post[];
  /** Greenhouses in the gardens quarter. */
  greenhouses: Post[];
}

// ---------------------------------------------------------------------------
// Deterministic noise
// ---------------------------------------------------------------------------

/** Integer hash → 0..1. Stable across platforms (32-bit integer math only). */
export function hash2(x: number, z: number, seed: number): number {
  let h = (Math.imul(x, 374761393) + Math.imul(z, 668265263) + Math.imul(seed, 1442695041)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Smooth value noise, 0..1. */
export function valueNoise(x: number, z: number, seed: number): number {
  const x0 = Math.floor(x);
  const z0 = Math.floor(z);
  const fx = x - x0;
  const fz = z - z0;
  const sx = fx * fx * (3 - 2 * fx);
  const sz = fz * fz * (3 - 2 * fz);
  const a = hash2(x0, z0, seed);
  const b = hash2(x0 + 1, z0, seed);
  const c = hash2(x0, z0 + 1, seed);
  const d = hash2(x0 + 1, z0 + 1, seed);
  return lerp(lerp(a, b, sx), lerp(c, d, sx), sz);
}

/** Three octaves of value noise, normalised to 0..1. */
export function fbm(x: number, z: number, seed: number, octaves = 3): number {
  let amp = 0.5;
  let freq = 1;
  let sum = 0;
  let norm = 0;
  for (let i = 0; i < octaves; i++) {
    sum += amp * valueNoise(x * freq, z * freq, seed + i * 101);
    norm += amp;
    amp *= 0.5;
    freq *= 2.1;
  }
  return sum / norm;
}

// ---------------------------------------------------------------------------
// Coordinate helpers
// ---------------------------------------------------------------------------

/** Centre of tile (tx, tz) in world metres. */
export function tileToWorld(tx: number, tz: number): [number, number] {
  return [(tx + 0.5 - CENTER_TILE) * TILE_M, (tz + 0.5 - CENTER_TILE) * TILE_M];
}

/** The tile under world point (x, z); may lie outside the map. */
export function worldToTile(x: number, z: number): [number, number] {
  return [Math.floor(x / TILE_M + CENTER_TILE), Math.floor(z / TILE_M + CENTER_TILE)];
}

export function inBounds(map: IslandMap, tx: number, tz: number): boolean {
  return tx >= 0 && tz >= 0 && tx < map.size && tz < map.size;
}

export function tileIndex(map: IslandMap, tx: number, tz: number): number {
  return tz * map.size + tx;
}

/** Ground height under world (x, z): the tile's level, water outside the map. */
export function groundY(map: IslandMap, x: number, z: number): number {
  const [tx, tz] = worldToTile(x, z);
  if (!inBounds(map, tx, tz)) return LEVEL_Y[0];
  const i = tileIndex(map, tx, tz);
  if (map.kind[i] === TileKind.dock) return DOCK_Y;
  return LEVEL_Y[map.level[i]];
}

export function isWalkable(map: IslandMap, tx: number, tz: number): boolean {
  if (!inBounds(map, tx, tz)) return false;
  return map.blocked[tileIndex(map, tx, tz)] === 0;
}

// ---------------------------------------------------------------------------
// Design: where things stand (tile units, from the island centre)
// ---------------------------------------------------------------------------

/** Radius of the open square in the middle of the village. */
export const PLAZA_RADIUS_TILES = 13;
/** Where the house ring stands. */
export const HOUSE_RING_TILES = 19;
/** The ring road around the houses. */
export const RING_ROAD_TILES = 25;
/** The hedge ring that closes the village, with gaps at the gates. */
export const HEDGE_RING_TILES = 29;
/** Half-width of a gate gap in the hedge ring, in tiles. */
export const GATE_GAP_TILES = 3;

/** The four spokes leave the square toward the quarters. `dir` is (dx, dz). */
const SPOKES: ReadonlyArray<{ dir: [number, number]; toTiles: number }> = [
  { dir: [0, -1], toTiles: 52 }, // north → archive
  { dir: [0, 1], toTiles: 52 }, // south → harbor
  { dir: [-1, 0], toTiles: 52 }, // west → workshop
  { dir: [1, 0], toTiles: 58 }, // east → lighthouse cape
];

/** Anchor tiles of the places (tile units, absolute). */
const PLACE_TILES: Record<PlaceId, [number, number]> = {
  market: [CENTER_TILE, CENTER_TILE],
  hub: [CENTER_TILE, CENTER_TILE - 20],
  archive: [CENTER_TILE, CENTER_TILE - 54],
  harbor: [CENTER_TILE, CENTER_TILE + 54],
  workshop: [CENTER_TILE - 54, CENTER_TILE],
  lighthouse: [CENTER_TILE + 62, CENTER_TILE],
  gardens: [CENTER_TILE + 30, CENTER_TILE + 34],
  solar: [CENTER_TILE - 32, CENTER_TILE - 32],
};

/** Half extents (tiles) of the flat plots each place is built on. */
const PLOT_HALF: Record<PlaceId, [number, number]> = {
  market: [0, 0],
  hub: [5, 4],
  archive: [6, 6],
  harbor: [7, 4],
  workshop: [8, 6],
  lighthouse: [3, 3],
  gardens: [9, 7],
  solar: [8, 6],
};

/** Building footprints (half extents, tiles) that block walking. */
const BUILDING_HALF: Partial<Record<PlaceId, [number, number]>> = {
  hub: [4, 3],
  archive: [3, 3],
  workshop: [6, 3],
  lighthouse: [1, 1],
};

/** Where the dock reaches into the bay: from the harbor plot southward. */
export const DOCK_TILES = { from: CENTER_TILE + 58, to: CENTER_TILE + 70, halfWidth: 1 } as const;

// ---------------------------------------------------------------------------
// Building the map
// ---------------------------------------------------------------------------

function fillRect(
  map: IslandMap,
  cx: number,
  cz: number,
  hw: number,
  hd: number,
  fn: (i: number) => void,
): void {
  for (let tz = cz - hd; tz <= cz + hd; tz++) {
    for (let tx = cx - hw; tx <= cx + hw; tx++) {
      if (inBounds(map, tx, tz)) fn(tileIndex(map, tx, tz));
    }
  }
}

function paintPlateau(map: IslandMap, i: number, kind: TileKind): void {
  map.level[i] = PLATEAU_LEVEL;
  map.kind[i] = kind;
  map.blocked[i] = 0;
}

/**
 * The terrain pass: island silhouette, bay, beaches, highland, rock, and the
 * flat market plateau in the middle.
 */
function buildTerrain(map: IslandMap): void {
  const C = CENTER_TILE;
  for (let tz = 0; tz < map.size; tz++) {
    for (let tx = 0; tx < map.size; tx++) {
      const i = tileIndex(map, tx, tz);
      const nx = (tx + 0.5 - C) / C;
      const nz = (tz + 0.5 - C) / C;
      const d = Math.hypot(nx, nz);
      const shapeNoise = fbm(nx * 1.8 + 3.1, nz * 1.8 + 7.7, ISLAND_SEED);
      const shape = d + (shapeNoise - 0.5) * 0.36;
      // The harbor bay bites into the south coast.
      const bayDist = Math.hypot(nx - 0.06, nz - 1.0);
      const inBay = bayDist < 0.27;
      const isLand = shape < 0.92 && !inBay;

      if (!isLand) {
        map.kind[i] = TileKind.water;
        map.level[i] = 0;
        map.blocked[i] = 1;
        continue;
      }

      const elev = 1 - d * d + (fbm(nx * 3.3 + 11, nz * 3.3 + 5, ISLAND_SEED + 7) - 0.5) * 0.5;
      const onEdge = shape > 0.8 || bayDist < 0.34;
      let level = onEdge ? 1 : elev > 0.72 ? 3 : 2;
      let kind: TileKind = level === 1 ? TileKind.sand : level === 3 ? TileKind.meadow : TileKind.grass;

      // The north-eastern highland turns to rock where it climbs highest.
      if (nx > 0.18 && nz < -0.18 && !onEdge) {
        const ridge = fbm(nx * 5 + 21, nz * 5 + 9, ISLAND_SEED + 3);
        if (elev + 0.35 * ridge > 1.02) {
          level = 4;
          kind = TileKind.rock;
        }
      }

      map.level[i] = level;
      map.kind[i] = kind;
      map.blocked[i] = level === 4 ? 1 : 0;
    }
  }

  // The market district is one flat plateau — the village needs level ground,
  // and a plateau with a low rim reads as "the town" from any distance.
  fillRect(map, C, C, MARKET_HALF_TILES + 4, MARKET_HALF_TILES + 4, (i) => {
    if (map.kind[i] === TileKind.water) return; // the sea stays the sea
    paintPlateau(map, i, TileKind.grass);
  });
}

/** Rasterise a filled disc of tiles around (cx, cz). */
function paintDisc(
  map: IslandMap,
  cx: number,
  cz: number,
  rMin: number,
  rMax: number,
  fn: (i: number, tx: number, tz: number) => void,
): void {
  const r = Math.ceil(rMax);
  for (let tz = cz - r; tz <= cz + r; tz++) {
    for (let tx = cx - r; tx <= cx + r; tx++) {
      if (!inBounds(map, tx, tz)) continue;
      const dist = Math.hypot(tx + 0.5 - cx, tz + 0.5 - cz);
      if (dist >= rMin && dist < rMax) fn(tileIndex(map, tx, tz), tx, tz);
    }
  }
}

/** The village: square, ring road, spokes, garden beds, plots and the dock. */
function buildVillage(map: IslandMap): void {
  const C = CENTER_TILE;

  // Open square with a ring of garden beds around it.
  paintDisc(map, C, C, 0, PLAZA_RADIUS_TILES, (i) => paintPlateau(map, i, TileKind.plaza));
  paintDisc(map, C, C, PLAZA_RADIUS_TILES, PLAZA_RADIUS_TILES + 1.5, (i, tx, tz) => {
    // Beds alternate with plaza so the square stays open toward every house.
    const a = Math.atan2(tz + 0.5 - C, tx + 0.5 - C);
    const bed = Math.floor(((a + Math.PI) / (2 * Math.PI)) * 16) % 2 === 0;
    paintPlateau(map, i, bed ? TileKind.garden : TileKind.plaza);
  });

  // Ring road around the houses.
  paintDisc(map, C, C, RING_ROAD_TILES - 1, RING_ROAD_TILES + 1, (i) =>
    paintPlateau(map, i, TileKind.path),
  );

  // Spokes from the square out to the quarters (width 3).
  for (const spoke of SPOKES) {
    for (let s = PLAZA_RADIUS_TILES - 1; s <= spoke.toTiles; s++) {
      for (let w = -1; w <= 1; w++) {
        const tx = C + spoke.dir[0] * s + spoke.dir[1] * w;
        const tz = C + spoke.dir[1] * s + spoke.dir[0] * w;
        if (!inBounds(map, tx, tz)) continue;
        const i = tileIndex(map, tx, tz);
        if (map.kind[i] === TileKind.water) continue;
        paintPlateau(map, i, TileKind.path);
      }
    }
  }

  // Flat plots for every place; the buildings on them block walking.
  for (const id of Object.keys(PLACE_TILES) as PlaceId[]) {
    const [px, pz] = PLACE_TILES[id];
    const [hw, hd] = PLOT_HALF[id];
    if (hw === 0 && hd === 0) continue;
    const kind =
      id === "gardens" ? TileKind.garden : id === "solar" ? TileKind.meadow : TileKind.plaza;
    fillRect(map, px, pz, hw, hd, (i) => {
      if (map.kind[i] === TileKind.water) return;
      paintPlateau(map, i, kind);
    });
    if (id === "lighthouse") {
      // The lighthouse stands on a rocky knob.
      fillRect(map, px, pz, hw, hd, (i) => {
        map.level[i] = 3;
        map.kind[i] = TileKind.rock;
        map.blocked[i] = 0;
      });
    }
    const bh = BUILDING_HALF[id];
    if (bh) fillRect(map, px, pz, bh[0], bh[1], (i) => (map.blocked[i] = 1));
  }

  // The dock runs from the harbor plot into the bay.
  for (let tz = DOCK_TILES.from; tz <= DOCK_TILES.to; tz++) {
    for (let tx = C - DOCK_TILES.halfWidth; tx <= C + DOCK_TILES.halfWidth; tx++) {
      if (!inBounds(map, tx, tz)) continue;
      const i = tileIndex(map, tx, tz);
      map.kind[i] = TileKind.dock;
      map.blocked[i] = 0;
    }
  }
}

/** The house ring: 16 slots, the four gate directions left open, the hub north. */
function placeHouses(): HousePlot[] {
  const houses: HousePlot[] = [];
  const variants: HouseVariant[] = ["solar-barrel", "garden-roof", "glass-loft"];
  const slots = 16;
  for (let k = 0; k < slots; k++) {
    if (k % 4 === 0) continue; // N, E, S, W are gates (N also holds the hub)
    const angle = (k / slots) * Math.PI * 2; // clockwise from north
    const r = HOUSE_RING_TILES * TILE_M;
    const x = Math.sin(angle) * r;
    const z = -Math.cos(angle) * r;
    const seed = hash2(k, 7, ISLAND_SEED);
    houses.push({
      x,
      z,
      w: 3,
      d: 2,
      // The door (local +z) faces the square: rotate so local +z points to the centre.
      rotation: Math.atan2(-x, -z),
      variant: variants[k % variants.length],
      seed,
    });
  }
  return houses;
}

/** Block the tiles under every house so walkers route around them. */
function blockHouses(map: IslandMap, houses: HousePlot[]): void {
  for (const h of houses) {
    // Rasterise the rotated footprint by sampling its four quadrants.
    const hw = (h.w * TILE_M) / 2;
    const hd = (h.d * TILE_M) / 2;
    const cos = Math.cos(h.rotation);
    const sin = Math.sin(h.rotation);
    for (let lx = -hw + 0.5; lx <= hw; lx += 1) {
      for (let lz = -hd + 0.5; lz <= hd; lz += 1) {
        const wx = h.x + lx * cos + lz * sin;
        const wz = h.z - lx * sin + lz * cos;
        const [tx, tz] = worldToTile(wx, wz);
        if (inBounds(map, tx, tz)) map.blocked[tileIndex(map, tx, tz)] = 1;
      }
    }
  }
}

/** The hedge ring with its four gates, and the lamps along ring and spokes. */
function placeRingFurniture(map: IslandMap): { hedges: Post[]; lamps: Post[] } {
  const hedges: Post[] = [];
  const lamps: Post[] = [];
  const r = HEDGE_RING_TILES * TILE_M;
  const circumference = 2 * Math.PI * r;
  const segments = Math.round(circumference / 2.2);
  for (let s = 0; s < segments; s++) {
    const angle = (s / segments) * Math.PI * 2;
    // A gate is a gap centred on each cardinal direction.
    const gapHalf = (GATE_GAP_TILES * TILE_M) / r;
    const toCardinal = Math.abs(((angle + Math.PI / 4) % (Math.PI / 2)) - Math.PI / 4);
    if (toCardinal < gapHalf) continue;
    const x = Math.sin(angle) * r;
    const z = -Math.cos(angle) * r;
    hedges.push({ x, z, y: groundY(map, x, z), rotation: -angle });
    const [tx, tz] = worldToTile(x, z);
    if (inBounds(map, tx, tz)) map.blocked[tileIndex(map, tx, tz)] = 1;
  }
  // Lamps: one at every gate post pair, and along the spokes every 8 tiles.
  for (const spoke of SPOKES) {
    for (let s = PLAZA_RADIUS_TILES + 3; s <= spoke.toTiles - 2; s += 8) {
      for (const side of [-2.5, 2.5]) {
        const x = (spoke.dir[0] * s + spoke.dir[1] * side) * TILE_M;
        const z = (spoke.dir[1] * s + spoke.dir[0] * side) * TILE_M;
        const y = groundY(map, x, z);
        if (y <= LEVEL_Y[0]) continue;
        lamps.push({ x, z, y, rotation: 0 });
      }
    }
  }
  return { hedges, lamps };
}

/** Solar panel rows and greenhouses — the solarpunk furniture of two quarters. */
function placeQuarterFurniture(map: IslandMap): { panels: Post[]; greenhouses: Post[] } {
  const panels: Post[] = [];
  const greenhouses: Post[] = [];
  const [sx, sz] = PLACE_TILES.solar;
  for (let row = -2; row <= 2; row++) {
    for (let col = -3; col <= 3; col++) {
      const tx = sx + col * 2;
      const tz = sz + row * 3;
      const [x, z] = tileToWorld(tx, tz);
      panels.push({ x, z, y: groundY(map, x, z), rotation: 0 });
      if (inBounds(map, tx, tz)) map.blocked[tileIndex(map, tx, tz)] = 1;
    }
  }
  const [gx, gz] = PLACE_TILES.gardens;
  for (let row = -1; row <= 1; row++) {
    for (let col = -1; col <= 1; col += 2) {
      const tx = gx + col * 4;
      const tz = gz + row * 4;
      const [x, z] = tileToWorld(tx, tz);
      greenhouses.push({ x, z, y: groundY(map, x, z), rotation: 0 });
      fillRect(map, tx, tz, 1, 1, (i) => (map.blocked[i] = 1));
    }
  }
  return { panels, greenhouses };
}

/** Trees wherever grass or meadow is free, away from roads and plots. */
function placeTrees(map: IslandMap): TreeSpot[] {
  const trees: TreeSpot[] = [];
  const C = CENTER_TILE;
  for (let tz = 1; tz < map.size - 1; tz++) {
    for (let tx = 1; tx < map.size - 1; tx++) {
      const i = tileIndex(map, tx, tz);
      const k = map.kind[i];
      if (k !== TileKind.grass && k !== TileKind.meadow) continue;
      if (map.blocked[i]) continue;
      const rTiles = Math.hypot(tx + 0.5 - C, tz + 0.5 - C);
      // Inside the village only a few trees, between the houses and the hedge.
      const inVillage = rTiles < HEDGE_RING_TILES + 1;
      if (inVillage && (rTiles < HOUSE_RING_TILES + 2 || rTiles > RING_ROAD_TILES + 1.5)) continue;
      const density = inVillage ? 0.05 : k === TileKind.meadow ? 0.13 : 0.07;
      if (hash2(tx, tz, ISLAND_SEED + 55) > density) continue;
      // Keep a clear margin to anything built or paved.
      let clear = true;
      for (let dz = -1; dz <= 1 && clear; dz++) {
        for (let dx = -1; dx <= 1; dx++) {
          const j = tileIndex(map, tx + dx, tz + dz);
          const kk = map.kind[j];
          if (kk === TileKind.path || kk === TileKind.plaza || kk === TileKind.dock || kk === TileKind.garden || (map.blocked[j] && kk !== TileKind.water && kk !== TileKind.rock)) {
            clear = false;
            break;
          }
        }
      }
      if (!clear) continue;
      const [x, z] = tileToWorld(tx, tz);
      trees.push({
        x: x + (hash2(tx, tz, 3) - 0.5) * 0.8,
        z: z + (hash2(tx, tz, 4) - 0.5) * 0.8,
        y: LEVEL_Y[map.level[i]],
        size: hash2(tx, tz, 5),
        shade: hash2(tx, tz, 6),
      });
      map.blocked[i] = 1;
    }
  }
  return trees;
}

function buildPlaces(map: IslandMap): Record<PlaceId, Place> {
  const C = CENTER_TILE;
  const facingCentre = (tx: number, tz: number) => Math.atan2(C - tx, C - tz);
  const place = (id: PlaceId, standTile: [number, number], facing: number): Place => ({
    id,
    tile: PLACE_TILES[id],
    standTile,
    facing,
  });
  const places: Record<PlaceId, Place> = {
    // The meeting spot: at the table under the tree, facing it.
    market: place("market", [C - 4, C + 3], Math.atan2(4, -3)),
    // In front of the hub's door, facing the door (north).
    hub: place("hub", [C, PLACE_TILES.hub[1] + 5], Math.PI),
    archive: place("archive", [C, PLACE_TILES.archive[1] + 5], Math.PI),
    harbor: place("harbor", [C, PLACE_TILES.harbor[1] + 2], 0),
    workshop: place("workshop", [PLACE_TILES.workshop[0] + 8, C], -Math.PI / 2),
    lighthouse: place("lighthouse", [PLACE_TILES.lighthouse[0] - 3, C], Math.PI / 2),
    gardens: place("gardens", [PLACE_TILES.gardens[0], PLACE_TILES.gardens[1]], 0),
    solar: place("solar", [PLACE_TILES.solar[0] + 1, PLACE_TILES.solar[1] + 8], Math.PI),
  };
  void facingCentre;
  // Make sure every stand tile is walkable — a place nobody can reach is a bug.
  for (const p of Object.values(places)) {
    const [tx, tz] = p.standTile;
    if (inBounds(map, tx, tz)) {
      const i = tileIndex(map, tx, tz);
      map.blocked[i] = 0;
      if (map.kind[i] === TileKind.water) paintPlateau(map, i, TileKind.path);
    }
  }
  return places;
}

/** The central tree and the round table block the middle of the square. */
function blockSquareFurniture(map: IslandMap): void {
  const C = CENTER_TILE;
  fillRect(map, C, C, 1, 1, (i) => (map.blocked[i] = 1)); // trunk
}

export interface Island {
  map: IslandMap;
  content: IslandContent;
}

let cached: Island | null = null;

/** Build (once) the whole island. Deterministic: same numbers every call. */
export function buildIsland(): Island {
  if (cached) return cached;
  const size = ISLAND_TILES;
  const map: IslandMap = {
    size,
    kind: new Uint8Array(size * size),
    level: new Uint8Array(size * size),
    blocked: new Uint8Array(size * size),
  };
  buildTerrain(map);
  buildVillage(map);
  const houses = placeHouses();
  blockHouses(map, houses);
  blockSquareFurniture(map);
  const { hedges, lamps } = placeRingFurniture(map);
  const { panels, greenhouses } = placeQuarterFurniture(map);
  const trees = placeTrees(map);
  const places = buildPlaces(map);
  cached = { map, content: { places, houses, trees, hedges, lamps, panels, greenhouses } };
  return cached;
}

/** Tests that need a fresh build. */
export function resetIslandCache(): void {
  cached = null;
}

// ---------------------------------------------------------------------------
// Pathfinding — A* over walkable tiles, eight neighbours, no corner cutting
// ---------------------------------------------------------------------------

const SQRT2 = Math.SQRT2;

class MinHeap {
  private keys: number[] = [];
  private vals: number[] = [];
  get size(): number {
    return this.keys.length;
  }
  push(key: number, val: number): void {
    this.keys.push(key);
    this.vals.push(val);
    let i = this.keys.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (this.keys[p] <= this.keys[i]) break;
      this.swap(i, p);
      i = p;
    }
  }
  pop(): number {
    const top = this.vals[0];
    const lastKey = this.keys.pop() as number;
    const lastVal = this.vals.pop() as number;
    if (this.keys.length > 0) {
      this.keys[0] = lastKey;
      this.vals[0] = lastVal;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let m = i;
        if (l < this.keys.length && this.keys[l] < this.keys[m]) m = l;
        if (r < this.keys.length && this.keys[r] < this.keys[m]) m = r;
        if (m === i) break;
        this.swap(i, m);
        i = m;
      }
    }
    return top;
  }
  private swap(a: number, b: number): void {
    const k = this.keys[a];
    this.keys[a] = this.keys[b];
    this.keys[b] = k;
    const v = this.vals[a];
    this.vals[a] = this.vals[b];
    this.vals[b] = v;
  }
}

/** A step between two tiles is allowed when both are walkable and at most one level apart. */
function canStep(map: IslandMap, from: number, tx: number, tz: number): boolean {
  if (!isWalkable(map, tx, tz)) return false;
  const to = tileIndex(map, tx, tz);
  return Math.abs(map.level[to] - map.level[from]) <= 1;
}

/**
 * Shortest path from tile `from` to tile `to`, as a list of tiles INCLUDING the
 * start; `null` when no path exists (or the search budget runs out). Costs are
 * 1 / √2 per step, octile heuristic, corner cutting between two blocked tiles
 * forbidden so figures never clip a house corner.
 */
export function findPath(
  map: IslandMap,
  from: [number, number],
  to: [number, number],
  maxExpansions = 40_000,
): Array<[number, number]> | null {
  if (!isWalkable(map, to[0], to[1]) || !isWalkable(map, from[0], from[1])) return null;
  const n = map.size * map.size;
  const start = tileIndex(map, from[0], from[1]);
  const goal = tileIndex(map, to[0], to[1]);
  if (start === goal) return [from];
  const g = new Float32Array(n).fill(Infinity);
  const parent = new Int32Array(n).fill(-1);
  const closed = new Uint8Array(n);
  const heap = new MinHeap();
  const h = (i: number) => {
    const dx = Math.abs((i % map.size) - to[0]);
    const dz = Math.abs(Math.floor(i / map.size) - to[1]);
    return Math.max(dx, dz) + (SQRT2 - 1) * Math.min(dx, dz);
  };
  g[start] = 0;
  heap.push(h(start), start);
  let expansions = 0;
  while (heap.size > 0) {
    const cur = heap.pop();
    if (closed[cur]) continue;
    if (cur === goal) break;
    closed[cur] = 1;
    if (++expansions > maxExpansions) return null;
    const cx = cur % map.size;
    const cz = Math.floor(cur / map.size);
    for (let dz = -1; dz <= 1; dz++) {
      for (let dx = -1; dx <= 1; dx++) {
        if (dx === 0 && dz === 0) continue;
        const nx = cx + dx;
        const nz = cz + dz;
        if (!canStep(map, cur, nx, nz)) continue;
        if (dx !== 0 && dz !== 0) {
          // No squeezing diagonally between two blocked tiles.
          if (!isWalkable(map, cx + dx, cz) || !isWalkable(map, cx, cz + dz)) continue;
        }
        const ni = tileIndex(map, nx, nz);
        if (closed[ni]) continue;
        const cost = g[cur] + (dx !== 0 && dz !== 0 ? SQRT2 : 1);
        if (cost < g[ni]) {
          g[ni] = cost;
          parent[ni] = cur;
          heap.push(cost + h(ni), ni);
        }
      }
    }
  }
  if (parent[goal] === -1) return null;
  const path: Array<[number, number]> = [];
  for (let i = goal; i !== -1; i = parent[i]) {
    path.push([i % map.size, Math.floor(i / map.size)]);
  }
  path.reverse();
  return path;
}

/**
 * String-pulling: drop every waypoint that can be skipped along a straight,
 * fully walkable line, so a figure walks a polyline instead of zig-zagging
 * tile edges. Line walkability is sampled every half tile.
 */
export function smoothPath(map: IslandMap, path: Array<[number, number]>): Array<[number, number]> {
  if (path.length <= 2) return path;
  const out: Array<[number, number]> = [path[0]];
  let anchor = 0;
  while (anchor < path.length - 1) {
    let far = anchor + 1;
    for (let j = path.length - 1; j > anchor + 1; j--) {
      if (lineIsWalkable(map, path[anchor], path[j])) {
        far = j;
        break;
      }
    }
    out.push(path[far]);
    anchor = far;
  }
  return out;
}

function lineIsWalkable(map: IslandMap, a: [number, number], b: [number, number]): boolean {
  const steps = Math.ceil(Math.max(Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1])) * 2);
  let last = tileIndex(map, a[0], a[1]);
  for (let s = 1; s <= steps; s++) {
    const t = s / steps;
    const tx = Math.floor(a[0] + 0.5 + (b[0] - a[0]) * t);
    const tz = Math.floor(a[1] + 0.5 + (b[1] - a[1]) * t);
    if (!isWalkable(map, tx, tz)) return false;
    const i = tileIndex(map, tx, tz);
    if (Math.abs(map.level[i] - map.level[last]) > 1) return false;
    last = i;
  }
  return true;
}

/** A random walkable tile within the open square, for idle wandering. */
export function randomPlazaTile(map: IslandMap, rng: () => number): [number, number] {
  for (let attempt = 0; attempt < 32; attempt++) {
    const angle = rng() * Math.PI * 2;
    const r = 3 + rng() * (PLAZA_RADIUS_TILES - 4);
    const tx = Math.floor(CENTER_TILE + Math.cos(angle) * r);
    const tz = Math.floor(CENTER_TILE + Math.sin(angle) * r);
    if (isWalkable(map, tx, tz)) return [tx, tz];
  }
  return [CENTER_TILE + 4, CENTER_TILE + 4];
}
