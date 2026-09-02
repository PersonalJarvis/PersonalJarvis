/**
 * The island as numbers — the pure model behind `components/society/world/`.
 *
 * Everything the renderer draws and the walkers walk on is derived here from a
 * fixed seed, so two windows (and a unit test) always see the SAME island.
 * Nothing in this file touches three.js or React; `islandLayout.test.ts` pins
 * the geometry the way `deckRoom.test.ts` pins the mission deck.
 *
 * Layout decisions (maintainer, 2026-09-01/02 — recorded in
 * docs/agent-society/world-art-direction.md):
 *
 *  - The island is a 16 × 16 grid of "fields". One field is what the viewer
 *    sees at the closest zoom without scrolling. Fields are the navigation
 *    unit, not a visible grid.
 *  - The central 4 × 4 fields are the MARKET DISTRICT: a round plateau with a
 *    village laid out like a ring — houses around one open square, the way a
 *    classic comic village stands around its meeting place — but built in a
 *    bright solarpunk language. The lead agent's hub stands on its own podium
 *    at the head of the village, behind the ring road, astride the hedge ring —
 *    the palace at the north gate — with the ring hubs (docks, forge, relay,
 *    cantina) in the house ring below it.
 *  - Around the plateau the island is a composed landscape of BIOMES at
 *    different heights, arranged for the camera (which looks toward the
 *    north-west): the mountain with its snow cap is the backdrop in the
 *    north-west, the archive tower crowns a terraced hill in the north, the
 *    alpine meadows roll over the north-east, the lighthouse stands on a
 *    rocky cape in the east, the tropical cove with its lagoon and palms fills
 *    the low south-east foreground, the harbor bay bites into the south, the
 *    orchard meadows cover the south-west and the dark forest the west.
 *  - The terrain is a continuous height field quantised into steps: every
 *    slope becomes a terrace, every steep place a cliff. Roads are GRADED —
 *    never more than one step per tile — so every place stays reachable on
 *    foot; cliffs of two or more steps are real barriers.
 *
 * Units: 1 world unit = 1 m (matches `lib/deckRoom.ts`). One tile is 2 m.
 * World origin is the island's centre; +x is east, +z is south, so the tile
 * with index (tx, tz) has its centre at ((tx + 0.5 − 128) · 2, (tz + 0.5 − 128) · 2).
 */

// ---------------------------------------------------------------------------
// Grid constants
// ---------------------------------------------------------------------------

/** Edge of one terrain tile in metres. */
export const TILE_M = 2;
/** Tiles along one edge of a "field" — the closest zoom shows about one field. */
export const FIELD_TILES = 16;
/** Fields along one island edge. */
export const ISLAND_FIELDS = 16;
/** Tiles along one island edge (256). */
export const ISLAND_TILES = FIELD_TILES * ISLAND_FIELDS;
/** Half the island edge in metres — the pan clamp. */
export const ISLAND_HALF_M = (ISLAND_TILES * TILE_M) / 2;
/** Fields along one edge of the central market district. */
export const MARKET_FIELDS = 4;
/** Tiles along one edge of the market district (64). */
export const MARKET_TILES = MARKET_FIELDS * FIELD_TILES;
/** Half the market edge in tiles, from the centre. */
export const MARKET_HALF_TILES = MARKET_TILES / 2;
/** Radius (tiles) of the round plateau the market district stands on. */
export const PLATEAU_RADIUS_TILES = MARKET_HALF_TILES + 7;
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
  /** Dark forest floor under the dense western woods. */
  forest: 9,
  /** High, sun-bleached alpine meadow. */
  alpine: 10,
  /** The mountain's snow cap. */
  snow: 11,
} as const;
export type TileKind = (typeof TileKind)[keyof typeof TileKind];

/**
 * Ground height per terrain level, in metres. Level 0 is the water surface;
 * the low steps are small enough that a walker crossing one reads as a step,
 * not a jump; the high steps belong to the mountain and the cliffs, where
 * nobody walks and the faces should tower.
 */
export const LEVEL_Y: readonly number[] = [0, 0.35, 1.0, 1.8, 2.8, 4.0, 5.5, 7.4, 9.8, 12.6];
/** The highest level index. */
export const MAX_LEVEL = LEVEL_Y.length - 1;
/** The level every road and plot of the village sits on — the walkable plateau. */
export const PLATEAU_LEVEL = 3;
/** The hub stands one step above the village. */
export const PODIUM_LEVEL = PLATEAU_LEVEL + 1;
/** Height of the wooden dock above the water. */
export const DOCK_Y = 0.55;
/** Level from which a rock tile becomes snow. */
export const SNOW_LEVEL = 8;

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
  | "solar"
  | "plugins"
  | "foundry"
  | "skills"
  | "mcp"
  | "cli";

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

/** What grows on a spot: the biome decides. */
export type TreeKind = "round" | "pine" | "palm";

export interface TreeSpot {
  x: number;
  z: number;
  /** Ground height under the trunk. */
  y: number;
  /** 0..1 — trunk height and canopy radius scale with it. */
  size: number;
  /** 0..1 — picks between the two canopy greens. */
  shade: number;
  kind: TreeKind;
}

export interface Boulder {
  x: number;
  z: number;
  y: number;
  /** 0..1 — radius scales with it. */
  size: number;
  /** 0..1 — a stable per-boulder rotation and shade pick. */
  seed: number;
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
  /** Rocks scattered over the highland, the cliffs and the beaches. */
  boulders: Boulder[];
  /** Hedge segments forming the village ring, with gaps at the four gates. */
  hedges: Post[];
  /** Light posts along the ring and the spokes. */
  lamps: Post[];
  /** Solar panel rows on the mountain's foot terrace (centre + rotation). */
  panels: Post[];
  /** Greenhouses in the gardens quarter. */
  greenhouses: Post[];
  /** Where World Kit buildings stand (metres) and how they turn; the front faces local +z. */
  kitPoses: Record<KitPlace, KitPose>;
}

/** Places that are World Kit buildings sitting IN the house ring (world-masterplan-v2.md §5). */
export type KitPlace = "plugins" | "foundry" | "skills" | "mcp" | "cli";

export interface KitPose {
  x: number;
  z: number;
  rotation: number;
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

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

function smoothstep(e0: number, e1: number, x: number): number {
  const t = clamp((x - e0) / (e1 - e0), 0, 1);
  return t * t * (3 - 2 * t);
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

/**
 * Ring slots (of 16) a kit building takes over instead of houses. A house slot
 * is ~15 m of arc, so a hall takes two neighbouring slots and stands centred
 * between them. The Agent Foundry sits on 2-3, the mirror image of the Plugin
 * Docks on 13-14 across the north axis: the two halls frame the square's north
 * side, and the foundry's ramp runs down onto the plaza in full view of the
 * island camera (which looks from the south-east).
 */
export const RING_KIT_SLOTS: Record<KitPlace, readonly number[]> = {
  plugins: [13, 14],
  foundry: [2, 3],
  // Hubs sit on the north and west of the ring so their fronts face the camera
  // (it looks from the south-east); houses take the slots whose backs it sees.
  skills: [15], // north, beside the docks: the Skill Forge
  mcp: [1], // north, beside the foundry: the Relay Tower
  cli: [11], // west: the Terminal Cantina
};

/**
 * Footprint of each kit building in TILES, as its GLB's `jarvis_building`
 * extras declare it. Walkers route around these the way they route around a
 * house; the foundry's conveyor ramp stays walkable on purpose — a figure
 * leaving the portal walks down it.
 */
export const KIT_FOOTPRINT_TILES: Record<KitPlace, { w: number; d: number }> = {
  plugins: { w: 8, d: 5 },
  foundry: { w: 10, d: 7 },
  skills: { w: 7, d: 5 },
  mcp: { w: 5, d: 5 },
  cli: { w: 7, d: 5 },
};

/**
 * Kit buildings face the square by default. The Agent Foundry does not: it
 * faces the ring road (south-east), because a factory's loading side belongs
 * on the road rather than on the market place — and because its door is the
 * one door a viewer has to SEE. The island camera is fixed at a 45° yaw from
 * the south-east, so this is the heading that puts the portal, the sign, the
 * assembly arms and the whole conveyor in plain view of the default camera.
 */
export const KIT_FACING: Partial<Record<KitPlace, number>> = { foundry: Math.PI / 4 };

/** Where a kit building stands and which way it turns — the one source. */
export function kitPose(place: KitPlace): KitPose {
  const pose = ringPose(RING_KIT_SLOTS[place]);
  const facing = KIT_FACING[place];
  return facing === undefined ? pose : { ...pose, rotation: facing };
}

/**
 * The hub's gate: the podium sits astride the hedge ring at the north, so the
 * gap there is as wide as the podium (the other three gates keep `GATE_GAP_TILES`).
 */
export const HUB_GATE_GAP_TILES = 10;

/** Pose of a building centred on the given ring slots, front toward the square. */
export function ringPose(slots: readonly number[], radiusTiles = HOUSE_RING_TILES): KitPose {
  const mean = slots.reduce((a, b) => a + b, 0) / slots.length;
  const angle = (mean / 16) * Math.PI * 2; // clockwise from north
  const r = radiusTiles * TILE_M;
  const x = Math.sin(angle) * r;
  const z = -Math.cos(angle) * r;
  return { x, z, rotation: Math.atan2(-x, -z) };
}

/** The four spokes leave the square toward the quarters. `dir` is (dx, dz). */
export const SPOKES: ReadonlyArray<{ dir: [number, number]; toTiles: number }> = [
  { dir: [0, -1], toTiles: 61 }, // north → archive hill
  { dir: [0, 1], toTiles: 62 }, // south → harbor
  { dir: [-1, 0], toTiles: 60 }, // west → workshop clearing
  { dir: [1, 0], toTiles: 80 }, // east → lighthouse cape
];

/** Anchor tiles of the places (tile units, absolute). */
const PLACE_TILES: Record<PlaceId, [number, number]> = {
  market: [CENTER_TILE, CENTER_TILE],
  hub: [CENTER_TILE, CENTER_TILE - 30],
  archive: [CENTER_TILE, CENTER_TILE - 66],
  harbor: [CENTER_TILE, CENTER_TILE + 64],
  workshop: [CENTER_TILE - 68, CENTER_TILE],
  lighthouse: [CENTER_TILE + 84, CENTER_TILE],
  gardens: [CENTER_TILE + 44, CENTER_TILE + 48],
  solar: [CENTER_TILE - 40, CENTER_TILE - 40],
  // Kit buildings stand in the house ring (RING_KIT_SLOTS); the tile is the ring pose's.
  plugins: ringTile("plugins"),
  foundry: ringTile("foundry"),
  skills: ringTile("skills"),
  mcp: ringTile("mcp"),
  cli: ringTile("cli"),
};

/**
 * The Agent Foundry's walk-out, in world metres: where a brand-new figure
 * appears (just behind the portal's containment field) and where the conveyor
 * ramp sets it down on the plaza. Both distances are the GLB's own
 * `jarvis_building` anchors — `door` 6.5 m and `ramp_end` 13.4 m ahead of the
 * origin — nudged so the figure starts inside the glow and lands past the belt.
 */
export const FOUNDRY_PORTAL_M = 5.2;
export const FOUNDRY_RAMP_END_M = 13.6;
/** Centre of the conveyor deck ahead of the origin, in metres. */
export const FOUNDRY_RAMP_MID_M = 9.5;
/** How high the conveyor carries a figure above the plaza, in metres. */
export const FOUNDRY_BELT_LIFT_M = 0.55;

export function foundryWalkOut(): {
  from: [number, number];
  to: [number, number];
  heading: number;
} {
  const p = kitPose("foundry");
  const fx = Math.sin(p.rotation);
  const fz = Math.cos(p.rotation);
  return {
    from: [p.x + fx * FOUNDRY_PORTAL_M, p.z + fz * FOUNDRY_PORTAL_M],
    to: [p.x + fx * FOUNDRY_RAMP_END_M, p.z + fz * FOUNDRY_RAMP_END_M],
    heading: p.rotation,
  };
}

/** The tile a ring kit building's origin falls on. */
function ringTile(place: KitPlace): [number, number] {
  const p = kitPose(place);
  return [Math.floor(p.x / TILE_M + CENTER_TILE), Math.floor(p.z / TILE_M + CENTER_TILE)];
}

/** Half extents (tiles) of the flat plots each place is built on. */
const PLOT_HALF: Record<PlaceId, [number, number]> = {
  market: [0, 0],
  hub: [9, 5],
  archive: [7, 7],
  harbor: [8, 4],
  workshop: [9, 6],
  lighthouse: [4, 4],
  gardens: [9, 7],
  solar: [8, 7],
  plugins: [0, 0], // no flat plot of its own: it sits on the village plateau
  foundry: [0, 0],
  skills: [0, 0],
  mcp: [0, 0],
  cli: [0, 0],
};

/** The terrain level each plot is flattened to — its terrace in the landscape. */
const PLOT_LEVEL: Record<PlaceId, number> = {
  market: PLATEAU_LEVEL,
  hub: PODIUM_LEVEL,
  archive: 5,
  harbor: 2,
  workshop: 3,
  lighthouse: 6,
  gardens: 2,
  solar: 4,
  plugins: PLATEAU_LEVEL,
  foundry: PLATEAU_LEVEL,
  skills: PLATEAU_LEVEL,
  mcp: PLATEAU_LEVEL,
  cli: PLATEAU_LEVEL,
};

/** Building footprints (half extents, tiles) that block walking. */
const BUILDING_HALF: Partial<Record<PlaceId, [number, number]>> = {
  hub: [6, 3],
  archive: [4, 3], // the Memory House: 8 x 6 tiles
  workshop: [6, 3],
  lighthouse: [1, 1],
};

/** Where the dock reaches into the bay: from the harbor plot southward. */
export const DOCK_TILES = { from: CENTER_TILE + 66, to: CENTER_TILE + 80, halfWidth: 1 } as const;

/**
 * The biome regions, in normalised island coordinates (−1..1 from the centre,
 * +x east, +z south). Each is a soft disc: `r` is where its influence ends.
 */
export const REGIONS = {
  /** The backdrop: the mountain with the snow cap, north-west. */
  mountain: { x: -0.5, z: -0.5, r: 0.42 },
  /** The terraced hill the archive tower crowns, north. */
  archiveHill: { x: 0, z: -0.52, r: 0.2 },
  /** Alpine meadows, north-east. */
  highland: { x: 0.45, z: -0.45, r: 0.38 },
  /** The lighthouse cape, east. */
  cape: { x: 0.72, z: 0, r: 0.25 },
  /** The tropical cove with the lagoon, south-east. */
  cove: { x: 0.5, z: 0.5, r: 0.34 },
  /** The lagoon inside the cove — sea water enclosed by a sandbar. */
  lagoon: { x: 0.5, z: 0.54, r: 0.075 },
  /** The harbor bay, south. */
  bay: { x: 0.05, z: 0.72, r: 0.2 },
  /** Orchard meadows, south-west. */
  orchard: { x: -0.45, z: 0.5, r: 0.3 },
  /** The dark forest, west. */
  forest: { x: -0.6, z: 0.02, r: 0.34 },
} as const;
export type RegionId = keyof typeof REGIONS;

/** Small rock islets offshore: sea stacks with foam around them. */
export const ISLETS: ReadonlyArray<{ x: number; z: number; r: number; level: number }> = [
  { x: 0.93, z: -0.22, r: 0.032, level: 4 },
  { x: 0.9, z: 0.24, r: 0.026, level: 3 },
  { x: -0.58, z: 0.8, r: 0.04, level: 5 },
  { x: -0.86, z: -0.6, r: 0.03, level: 6 },
];

// ---------------------------------------------------------------------------
// Building the map
// ---------------------------------------------------------------------------

function fillRect(
  map: IslandMap,
  cx: number,
  cz: number,
  hw: number,
  hd: number,
  fn: (i: number, tx: number, tz: number) => void,
): void {
  for (let tz = cz - hd; tz <= cz + hd; tz++) {
    for (let tx = cx - hw; tx <= cx + hw; tx++) {
      if (inBounds(map, tx, tz)) fn(tileIndex(map, tx, tz), tx, tz);
    }
  }
}

/** Rasterise a filled disc (or ring) of tiles around (cx, cz). */
function paintDisc(
  map: IslandMap,
  cx: number,
  cz: number,
  rMin: number,
  rMax: number,
  fn: (i: number, tx: number, tz: number) => void,
): void {
  const r = Math.ceil(rMax);
  for (let tz = Math.floor(cz - r); tz <= Math.ceil(cz + r); tz++) {
    for (let tx = Math.floor(cx - r); tx <= Math.ceil(cx + r); tx++) {
      if (!inBounds(map, tx, tz)) continue;
      const dist = Math.hypot(tx + 0.5 - cx, tz + 0.5 - cz);
      if (dist >= rMin && dist < rMax) fn(tileIndex(map, tx, tz), tx, tz);
    }
  }
}

function paintLand(map: IslandMap, i: number, kind: TileKind, level: number): void {
  map.level[i] = level;
  map.kind[i] = kind;
  map.blocked[i] = kind === TileKind.rock || kind === TileKind.snow ? 1 : 0;
}

function paintWater(map: IslandMap, i: number): void {
  map.kind[i] = TileKind.water;
  map.level[i] = 0;
  map.blocked[i] = 1;
}

/** Normalised distance (0 at the centre, 1 at the edge) to a region. */
function regionT(nx: number, nz: number, region: { x: number; z: number; r: number }): number {
  return Math.hypot(nx - region.x, nz - region.z) / region.r;
}

/** A soft bump: 1 at the centre, 0 at the region's edge, rounded shoulders. */
function bump(nx: number, nz: number, region: { x: number; z: number; r: number }, power = 1.3): number {
  const t = regionT(nx, nz, region);
  return t >= 1 ? 0 : Math.pow(1 - t, power);
}

/**
 * The continuous height field, in levels (float). Composed, not rolled: a
 * radial fall-off toward the sea, the designed features of every biome on top,
 * then a little noise so the terraces meander instead of running in circles.
 */
export function heightAt(nx: number, nz: number): number {
  const d = Math.hypot(nx, nz * 1.04);
  // Level ~2 at the coast (a narrow beach where the noise dips), ~3.6 inland.
  let h = 1.2 + 2.4 * (1 - Math.pow(d, 1.8));
  h += 7.2 * bump(nx, nz, REGIONS.mountain, 1.4);
  h += 2.7 * bump(nx, nz, REGIONS.archiveHill, 1.1);
  h += 2.0 * bump(nx, nz, REGIONS.highland, 1.0);
  h += 4.4 * bump(nx, nz, REGIONS.cape, 1.2);
  h -= 1.7 * bump(nx, nz, REGIONS.cove, 1.0);
  h -= 1.3 * bump(nx, nz, REGIONS.bay, 0.8);
  h -= 0.7 * bump(nx, nz, REGIONS.orchard, 1.0);
  h += 1.0 * bump(nx, nz, REGIONS.forest, 1.0);
  h += (fbm(nx * 3.3 + 11, nz * 3.3 + 5, ISLAND_SEED + 7) - 0.5) * 1.5;
  // The market plateau: dead flat, blended in over a wide skirt of terraces.
  const plateauT = (Math.hypot(nx, nz) * CENTER_TILE) / PLATEAU_RADIUS_TILES;
  const w = 1 - smoothstep(1.0, 1.55, plateauT);
  return lerp(h, PLATEAU_LEVEL, w);
}

/** Whether normalised (nx, nz) is island (true) or sea (false). */
export function isLandAt(nx: number, nz: number): boolean {
  const d = Math.hypot(nx, nz * 1.04);
  const shapeNoise = fbm(nx * 1.8 + 3.1, nz * 1.8 + 7.7, ISLAND_SEED);
  let shape = d + (shapeNoise - 0.5) * 0.34;
  // The cape reaches out into the sea; the mountain's coast bulges.
  shape -= 0.14 * bump(nx, nz, { x: 0.9, z: 0, r: 0.3 }, 1.0);
  shape -= 0.06 * bump(nx, nz, REGIONS.mountain, 1.0);
  if (shape >= 0.86) return false;
  if (regionT(nx, nz, REGIONS.bay) < 1) return false;
  if (regionT(nx, nz, REGIONS.lagoon) < 1) return false;
  // The lagoon's channel to the sea, south-east of it.
  const cx = nx - REGIONS.lagoon.x;
  const cz = nz - REGIONS.lagoon.z;
  const along = (cx + cz) * Math.SQRT1_2;
  const across = Math.abs(cx - cz) * Math.SQRT1_2;
  if (along > 0 && along < 0.4 && across < 0.022) return false;
  return true;
}

/** The kind a land tile gets from its level and the biome it lies in. */
function biomeKind(nx: number, nz: number, level: number): TileKind {
  if (level >= SNOW_LEVEL) return TileKind.snow;
  if (level >= 7) return TileKind.rock;
  const inMountain = regionT(nx, nz, REGIONS.mountain) < 1;
  const inCape = regionT(nx, nz, REGIONS.cape) < 1;
  const inHighland = regionT(nx, nz, REGIONS.highland) < 1;
  const inCove = regionT(nx, nz, REGIONS.cove) < 0.8;
  const inForest = regionT(nx, nz, REGIONS.forest) < 0.92;
  const grain = fbm(nx * 6 + 31, nz * 6 + 17, ISLAND_SEED + 3);
  if (level === 6) return inMountain || inCape ? TileKind.rock : TileKind.alpine;
  if (level === 5) {
    if ((inMountain || inCape) && grain > 0.55) return TileKind.rock;
    return inMountain || inHighland || inCape ? TileKind.alpine : TileKind.meadow;
  }
  if (level === 1) return TileKind.sand;
  if (level === 2) {
    if (inCove) return TileKind.sand;
    return grain > 0.6 ? TileKind.meadow : TileKind.grass;
  }
  // Levels 3 and 4: the rolling middle ground.
  if (inForest && grain > 0.3) return TileKind.forest;
  if (level === 4) return inHighland && grain > 0.5 ? TileKind.alpine : TileKind.meadow;
  return grain > 0.66 ? TileKind.meadow : TileKind.grass;
}

/**
 * The terrain pass: island silhouette, bay, lagoon, the height field
 * quantised into levels, biome kinds, flower fields, islets.
 */
function buildTerrain(map: IslandMap): void {
  const C = CENTER_TILE;
  for (let tz = 0; tz < map.size; tz++) {
    for (let tx = 0; tx < map.size; tx++) {
      const i = tileIndex(map, tx, tz);
      const nx = (tx + 0.5 - C) / C;
      const nz = (tz + 0.5 - C) / C;
      if (!isLandAt(nx, nz)) {
        paintWater(map, i);
        continue;
      }
      const level = clamp(Math.round(heightAt(nx, nz)), 1, MAX_LEVEL);
      let kind = biomeKind(nx, nz, level);
      // Flower fields: drifts of blossom over the meadows, clustered by noise.
      if (
        (kind === TileKind.meadow || kind === TileKind.alpine) &&
        fbm(nx * 9 + 3, nz * 9 + 41, ISLAND_SEED + 13) > 0.64 &&
        hash2(tx, tz, 77) < 0.55
      ) {
        kind = TileKind.garden;
      }
      paintLand(map, i, kind, level);
    }
  }

  // Sandbar around the lagoon: a bright rim of beach, one step above the water.
  const lag = REGIONS.lagoon;
  paintDisc(map, C + lag.x * C, C + lag.z * C, 0, lag.r * C + 4.5, (i) => {
    if (map.kind[i] !== TileKind.water) paintLand(map, i, TileKind.sand, 1);
  });

  // Beach around the bay.
  const bay = REGIONS.bay;
  paintDisc(map, C + bay.x * C, C + bay.z * C, 0, bay.r * C + 3.5, (i) => {
    if (map.kind[i] !== TileKind.water && map.level[i] > 1) paintLand(map, i, TileKind.sand, 1);
  });

  // Islets: rock stacks with a green crown, so the sea is not empty.
  for (const islet of ISLETS) {
    const cx = C + islet.x * C;
    const cz = C + islet.z * C;
    const r = islet.r * C;
    paintDisc(map, cx, cz, 0, r, (i, tx, tz) => {
      const wobble = (hash2(tx, tz, 21) - 0.5) * 0.8;
      const dist = Math.hypot(tx + 0.5 - cx, tz + 0.5 - cz) + wobble;
      if (dist > r) return;
      if (dist < r * 0.45) paintLand(map, i, TileKind.grass, islet.level);
      else paintLand(map, i, TileKind.rock, Math.max(1, islet.level - (dist > r * 0.75 ? 2 : 1)));
    });
  }

  // The breakwater sheltering the harbor: a line of rock in the bay's mouth.
  for (let t = 0; t < 14; t++) {
    const tx = C + 18 + t;
    const tz = C + 96 - Math.round(t * 0.55);
    fillRect(map, tx, tz, 0, 1, (i) => {
      if (map.kind[i] === TileKind.water) paintLand(map, i, TileKind.rock, 2);
    });
  }
}

/** Flatten every place's plot to its terrace level. */
function buildPlots(map: IslandMap): void {
  for (const id of Object.keys(PLACE_TILES) as PlaceId[]) {
    const [px, pz] = PLACE_TILES[id];
    const [hw, hd] = PLOT_HALF[id];
    if (hw === 0 && hd === 0) continue;
    const kind =
      id === "gardens"
        ? TileKind.garden
        : id === "solar"
          ? TileKind.meadow
          : id === "lighthouse"
            ? TileKind.rock
            : TileKind.plaza;
    fillRect(map, px, pz, hw, hd, (i) => {
      if (map.kind[i] === TileKind.water) return;
      paintLand(map, i, kind, PLOT_LEVEL[id]);
      if (id === "lighthouse") map.blocked[i] = 0; // the keeper walks the knob
    });
  }
}

/**
 * A road that climbs at most one level per tile: walked outward from the
 * square, each tile clamps to its predecessor ±1, and the shoulders beside it
 * come along, so the road never runs in a trench. This is what keeps every
 * place reachable on foot whatever the terrain does.
 */
function gradeSpoke(map: IslandMap, dir: [number, number], fromTiles: number, toTiles: number): void {
  const C = CENTER_TILE;
  let prev = PLATEAU_LEVEL;
  for (let s = fromTiles; s <= toTiles; s++) {
    const cx = C + dir[0] * s;
    const cz = C + dir[1] * s;
    if (!inBounds(map, cx, cz)) break;
    const ci = tileIndex(map, cx, cz);
    const desired = map.kind[ci] === TileKind.water ? prev : map.level[ci];
    const lvl = clamp(desired, prev - 1, prev + 1);
    for (let w = -2; w <= 2; w++) {
      const tx = cx + dir[1] * w;
      const tz = cz + dir[0] * w;
      if (!inBounds(map, tx, tz)) continue;
      const i = tileIndex(map, tx, tz);
      if (map.kind[i] === TileKind.water) continue;
      if (Math.abs(w) <= 1) {
        paintLand(map, i, TileKind.path, lvl);
      } else if (Math.abs(map.level[i] - lvl) > 1) {
        map.level[i] = lvl + Math.sign(map.level[i] - lvl);
      }
    }
    prev = lvl;
  }
}

/** The village: square, ring road, spokes, garden beds and the dock. */
function buildVillage(map: IslandMap): void {
  const C = CENTER_TILE;

  // Open square with a ring of garden beds around it.
  paintDisc(map, C, C, 0, PLAZA_RADIUS_TILES, (i) => paintLand(map, i, TileKind.plaza, PLATEAU_LEVEL));
  paintDisc(map, C, C, PLAZA_RADIUS_TILES, PLAZA_RADIUS_TILES + 1.5, (i, tx, tz) => {
    // Beds alternate with plaza so the square stays open toward every house.
    const a = Math.atan2(tz + 0.5 - C, tx + 0.5 - C);
    const bed = Math.floor(((a + Math.PI) / (2 * Math.PI)) * 16) % 2 === 0;
    paintLand(map, i, bed ? TileKind.garden : TileKind.plaza, PLATEAU_LEVEL);
  });

  // Ring road around the houses — it climbs the hub's podium rather than
  // vanishing under it: the kind changes, the level stays.
  paintDisc(map, C, C, RING_ROAD_TILES - 1, RING_ROAD_TILES + 1, (i) => {
    const lvl = map.level[i] === PODIUM_LEVEL ? PODIUM_LEVEL : PLATEAU_LEVEL;
    paintLand(map, i, TileKind.path, lvl);
  });

  // Spokes from the square out to the quarters (width 3), graded.
  for (const spoke of SPOKES) gradeSpoke(map, spoke.dir, PLAZA_RADIUS_TILES - 1, spoke.toTiles);

  // Buildings block walking; their tiles keep the plot level.
  for (const id of Object.keys(BUILDING_HALF) as PlaceId[]) {
    const bh = BUILDING_HALF[id];
    if (!bh) continue;
    const [px, pz] = PLACE_TILES[id];
    fillRect(map, px, pz, bh[0], bh[1], (i) => {
      map.blocked[i] = 1;
      map.level[i] = PLOT_LEVEL[id];
    });
  }

  // The dock runs from the harbor plot into the bay, one step above the water
  // so a walker can step onto the planks from the beach.
  for (let tz = DOCK_TILES.from; tz <= DOCK_TILES.to; tz++) {
    for (let tx = C - DOCK_TILES.halfWidth; tx <= C + DOCK_TILES.halfWidth; tx++) {
      if (!inBounds(map, tx, tz)) continue;
      const i = tileIndex(map, tx, tz);
      map.kind[i] = TileKind.dock;
      map.level[i] = 1;
      map.blocked[i] = 0;
    }
  }
  // A small jetty into the lagoon, from its northern sandbar.
  const lag = REGIONS.lagoon;
  const jx = Math.round(C + lag.x * C);
  const jz0 = Math.round(C + lag.z * C - lag.r * C) - 2;
  for (let tz = jz0; tz <= jz0 + 6; tz++) {
    if (!inBounds(map, jx, tz)) continue;
    const i = tileIndex(map, jx, tz);
    if (map.kind[i] !== TileKind.water && map.kind[i] !== TileKind.sand) continue;
    map.kind[i] = TileKind.dock;
    map.level[i] = 1;
    map.blocked[i] = 0;
  }
}

/** The house ring: 16 slots, the four gate directions left open, the hub north. */
function placeHouses(): HousePlot[] {
  const houses: HousePlot[] = [];
  const variants: HouseVariant[] = ["solar-barrel", "garden-roof", "glass-loft"];
  const slots = 16;
  const taken = new Set(Object.values(RING_KIT_SLOTS).flat());
  for (let k = 0; k < slots; k++) {
    if (k % 4 === 0) continue; // N, E, S, W are gates (N also holds the hub)
    if (taken.has(k)) continue; // a kit building stands here
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
    // A gate is a gap centred on each cardinal direction; the north gate is
    // the hub's podium, so its gap is as wide as the podium.
    const toNorth = Math.min(angle, Math.PI * 2 - angle);
    const gapTiles = toNorth < Math.PI / 4 ? HUB_GATE_GAP_TILES : GATE_GAP_TILES;
    const gapHalf = (gapTiles * TILE_M) / r;
    const toCardinal = Math.abs(((angle + Math.PI / 4) % (Math.PI / 2)) - Math.PI / 4);
    if (toCardinal < gapHalf) continue;
    const x = Math.sin(angle) * r;
    const z = -Math.cos(angle) * r;
    hedges.push({ x, z, y: groundY(map, x, z), rotation: -angle });
    const [tx, tz] = worldToTile(x, z);
    if (inBounds(map, tx, tz)) map.blocked[tileIndex(map, tx, tz)] = 1;
  }
  // Lamps: along the spokes every 8 tiles, on the road's shoulders.
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

/** Which tree grows on a tile, and how densely, per biome. `null` = none. */
function treeChoice(map: IslandMap, tx: number, tz: number): { kind: TreeKind; density: number } | null {
  const i = tileIndex(map, tx, tz);
  const k = map.kind[i];
  const level = map.level[i];
  const nx = (tx + 0.5 - CENTER_TILE) / CENTER_TILE;
  const nz = (tz + 0.5 - CENTER_TILE) / CENTER_TILE;
  const grain = hash2(tx, tz, 8);
  if (k === TileKind.forest) return { kind: grain < 0.72 ? "round" : "pine", density: 0.34 };
  if (k === TileKind.alpine) return { kind: "pine", density: 0.08 };
  if (k === TileKind.sand) {
    // Palms only in the tropical cove, thickest around the lagoon.
    const cove = regionT(nx, nz, REGIONS.cove);
    if (cove >= 1) return null;
    const lagoon = regionT(nx, nz, REGIONS.lagoon);
    return { kind: "palm", density: lagoon < 1.9 ? 0.11 : 0.045 };
  }
  if (k === TileKind.meadow) return { kind: level >= 5 ? "pine" : "round", density: 0.09 };
  if (k === TileKind.grass) {
    // Islets carry a tree or two; the mainland's grass stays open.
    const offshore = Math.hypot(nx, nz) > 0.8;
    return { kind: "round", density: offshore ? 0.5 : 0.05 };
  }
  return null;
}

/** Trees wherever their biome allows, away from roads and plots. */
function placeTrees(map: IslandMap): TreeSpot[] {
  const trees: TreeSpot[] = [];
  const C = CENTER_TILE;
  for (let tz = 1; tz < map.size - 1; tz++) {
    for (let tx = 1; tx < map.size - 1; tx++) {
      const i = tileIndex(map, tx, tz);
      if (map.blocked[i]) continue;
      const choice = treeChoice(map, tx, tz);
      if (!choice) continue;
      const rTiles = Math.hypot(tx + 0.5 - C, tz + 0.5 - C);
      // Inside the village only a few trees, between the houses and the hedge.
      const inVillage = rTiles < HEDGE_RING_TILES + 1;
      if (inVillage && (rTiles < HOUSE_RING_TILES + 2 || rTiles > RING_ROAD_TILES + 1.5)) continue;
      const density = inVillage ? 0.05 : choice.density;
      if (hash2(tx, tz, ISLAND_SEED + 55) > density) continue;
      // Keep a clear margin to anything built or paved.
      let clear = true;
      for (let dz = -1; dz <= 1 && clear; dz++) {
        for (let dx = -1; dx <= 1; dx++) {
          const j = tileIndex(map, tx + dx, tz + dz);
          const kk = map.kind[j];
          if (
            kk === TileKind.path ||
            kk === TileKind.plaza ||
            kk === TileKind.dock ||
            (map.blocked[j] && kk !== TileKind.water && kk !== TileKind.rock && kk !== TileKind.snow)
          ) {
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
        kind: choice.kind,
      });
      map.blocked[i] = 1;
    }
  }
  return trees;
}

/** Boulders on the high ground, along the cliffs and on the beaches. */
function placeBoulders(map: IslandMap): Boulder[] {
  const boulders: Boulder[] = [];
  const C = CENTER_TILE;
  for (let tz = 1; tz < map.size - 1; tz++) {
    for (let tx = 1; tx < map.size - 1; tx++) {
      const i = tileIndex(map, tx, tz);
      const k = map.kind[i];
      if (k === TileKind.water || k === TileKind.path || k === TileKind.plaza || k === TileKind.dock) continue;
      if (Math.hypot(tx + 0.5 - C, tz + 0.5 - C) < HEDGE_RING_TILES + 4) continue;
      let density: number;
      if (k === TileKind.rock || k === TileKind.snow) density = 0.05;
      else if (k === TileKind.alpine) density = 0.035;
      else if (k === TileKind.sand) density = 0.012;
      else density = 0.008;
      // Rocks gather at the foot of cliffs: a higher neighbour raises the odds.
      let cliff = false;
      for (const [dx, dz] of [
        [1, 0],
        [-1, 0],
        [0, 1],
        [0, -1],
      ] as const) {
        const j = tileIndex(map, tx + dx, tz + dz);
        if (map.kind[j] !== TileKind.water && map.level[j] >= map.level[i] + 2) cliff = true;
      }
      if (cliff) density *= 3;
      if (hash2(tx, tz, ISLAND_SEED + 91) > density) continue;
      if (map.blocked[i] && k !== TileKind.rock && k !== TileKind.snow) continue; // a tree or a building
      const [x, z] = tileToWorld(tx, tz);
      boulders.push({
        x: x + (hash2(tx, tz, 31) - 0.5) * 1.2,
        z: z + (hash2(tx, tz, 32) - 0.5) * 1.2,
        y: LEVEL_Y[map.level[i]],
        size: hash2(tx, tz, 33),
        seed: hash2(tx, tz, 34),
      });
      map.blocked[i] = 1;
    }
  }
  return boulders;
}

/** A stand point `ahead` metres in front of a kit building, facing it. */
function kitPlace(id: KitPlace, ahead: number): Place {
  const p = kitPose(id);
  const tile = worldToTile(p.x + Math.sin(p.rotation) * ahead, p.z + Math.cos(p.rotation) * ahead);
  return { id, tile: PLACE_TILES[id], standTile: tile, facing: p.rotation + Math.PI };
}

function buildPlaces(map: IslandMap): Record<PlaceId, Place> {
  const C = CENTER_TILE;
  const place = (id: PlaceId, standTile: [number, number], facing: number): Place => ({
    id,
    tile: PLACE_TILES[id],
    standTile,
    facing,
  });
  const places: Record<PlaceId, Place> = {
    // The meeting spot: at the table under the tree, facing it.
    market: place("market", [C - 4, C + 3], Math.atan2(4, -3)),
    // On the square at the foot of the hub's grand stair, facing the door (north).
    hub: place("hub", [C, PLACE_TILES.hub[1] + 8], Math.PI),
    archive: place("archive", [C, PLACE_TILES.archive[1] + 5], Math.PI),
    harbor: place("harbor", [C, PLACE_TILES.harbor[1] - 2], 0),
    workshop: place("workshop", [PLACE_TILES.workshop[0] + 8, C], -Math.PI / 2),
    lighthouse: place("lighthouse", [PLACE_TILES.lighthouse[0] - 4, C], Math.PI / 2),
    gardens: place("gardens", [PLACE_TILES.gardens[0], PLACE_TILES.gardens[1]], 0),
    solar: place("solar", [PLACE_TILES.solar[0] + 1, PLACE_TILES.solar[1] + 9], Math.PI),
    // In front of the docks' bays (7.5 m toward the square), facing the building.
    plugins: kitPlace("plugins", 7.5),
    // At the foot of the foundry's conveyor ramp, facing the portal.
    foundry: kitPlace("foundry", 13.5),
    skills: kitPlace("skills", 7.5),
    mcp: kitPlace("mcp", 7.0),
    cli: kitPlace("cli", 7.5),
  };
  // Make sure every stand tile is walkable — a place nobody can reach is a bug.
  for (const p of Object.values(places)) {
    const [tx, tz] = p.standTile;
    if (inBounds(map, tx, tz)) {
      const i = tileIndex(map, tx, tz);
      map.blocked[i] = 0;
      if (map.kind[i] === TileKind.water) paintLand(map, i, TileKind.path, 1);
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
  buildPlots(map);
  buildVillage(map);
  const houses = placeHouses();
  blockHouses(map, houses);
  const kitPoses: Record<KitPlace, KitPose> = {
    plugins: kitPose("plugins"),
    foundry: kitPose("foundry"),
    skills: kitPose("skills"),
    mcp: kitPose("mcp"),
    cli: kitPose("cli"),
  };
  // Every kit footprint blocks walking the way a house does.
  blockHouses(
    map,
    (Object.keys(kitPoses) as KitPlace[]).map((id) => ({
      ...kitPoses[id],
      ...KIT_FOOTPRINT_TILES[id],
      variant: "glass-loft" as HouseVariant,
      seed: 0,
    })),
  );
  // The foundry's conveyor deck is furniture, not floor: it keeps trees off
  // and keeps strollers beside it. A newborn rides it on fixed waypoints, so
  // the block costs the entrance nothing.
  const belt = kitPose("foundry");
  blockHouses(map, [
    {
      x: belt.x + Math.sin(belt.rotation) * FOUNDRY_RAMP_MID_M,
      z: belt.z + Math.cos(belt.rotation) * FOUNDRY_RAMP_MID_M,
      rotation: belt.rotation,
      w: 3,
      d: 3,
      variant: "glass-loft" as HouseVariant,
      seed: 0,
    },
  ]);
  blockSquareFurniture(map);
  const { hedges, lamps } = placeRingFurniture(map);
  const { panels, greenhouses } = placeQuarterFurniture(map);
  const trees = placeTrees(map);
  const boulders = placeBoulders(map);
  const places = buildPlaces(map);
  cached = {
    map,
    content: { places, houses, trees, boulders, hedges, lamps, panels, greenhouses, kitPoses },
  };
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
 * forbidden so figures never clip a house corner. The budget covers a walk
 * from one coast to the other on the 256-tile island.
 */
export function findPath(
  map: IslandMap,
  from: [number, number],
  to: [number, number],
  maxExpansions = 160_000,
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
