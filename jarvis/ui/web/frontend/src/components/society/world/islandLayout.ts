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
 *    orchard meadows and farmland cover the south-west, a marsh with pools
 *    and reeds the south-western coast, dry savanna the eastern lowland, the
 *    dark forest the west; heather drifts over the high moor, gravel over the
 *    mountain's flanks, and a mine is cut into a cliff on its south-eastern
 *    side, reached by a branch off the north road.
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
  /** Heather over the high moor, north-east. */
  heath: 12,
  /** Sun-dried savanna grass on the eastern lowland. */
  dry: 13,
  /** Crop rows of the south-western farmland. */
  farm: 14,
  /** Wetland with pools and reeds on the south-western coast. */
  marsh: 15,
  /** Loose gravel on the mountain's flanks. */
  scree: 16,
  /** The quarry floor around the mine. */
  quarry: 17,
  /** Still water in the marsh: a pool at sea level, no surf, no foam. */
  pool: 18,
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
/** The terrace the Agent Foundry stands on: the mountain's top step. */
export const SUMMIT_LEVEL = 9;

export interface IslandMap {
  /** Tiles along one edge. */
  size: number;
  /** `TileKind` per tile, row-major (index = tz * size + tx). */
  kind: Uint8Array;
  /** Terrain level per tile (index into `LEVEL_Y`). */
  level: Uint8Array;
  /** 1 where a walker may not stand: water, rock, buildings, tree trunks. */
  blocked: Uint8Array;
  /**
   * `blocked` without the buildings a viewer may turn (houses, ring hubs, the
   * foundry's belt). `applyBuildingYaws` stamps those back in at their current
   * heading, so a turned house blocks the tiles it actually covers.
   */
  blockedStatic: Uint8Array;
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
  | "mine"
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
  /** Ring slot (0..15, clockwise from north) — the house's stable identity. */
  slot: number;
  /** Centre of the footprint, world metres. */
  x: number;
  z: number;
  /** Footprint in tiles (w along the house's local x, d along local z). */
  w: number;
  d: number;
  /** Rotation about y; the door faces local +z. The viewer may turn it (`applyBuildingYaws`). */
  rotation: number;
  /** The heading the house rests at — what "reset" returns to. */
  defaultRotation: number;
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
  /** Reeds standing in the marsh. */
  reeds: Post[];
  /** Poles around the square that carry the festoon lights. */
  festoonPoles: Post[];
  /** The campfire on the cove's beach. */
  campfire: Post;
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

/**
 * The walkable tile closest to (tx, tz) within `maxRadius` rings, the tile
 * itself included; null when everything around is blocked. A figure that
 * finds a building turned over its head steps to this tile first.
 */
export function nearestWalkable(map: IslandMap, tx: number, tz: number, maxRadius = 8): [number, number] | null {
  if (isWalkable(map, tx, tz)) return [tx, tz];
  for (let r = 1; r <= maxRadius; r++) {
    let best: [number, number] | null = null;
    let bestD = Infinity;
    for (let dz = -r; dz <= r; dz++) {
      for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dz)) !== r) continue;
        if (!isWalkable(map, tx + dx, tz + dz)) continue;
        const d = dx * dx + dz * dz;
        if (d < bestD) {
          bestD = d;
          best = [tx + dx, tz + dz];
        }
      }
    }
    if (best) return best;
  }
  return null;
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
export const RING_KIT_SLOTS: Partial<Record<KitPlace, readonly number[]>> = {
  plugins: [13, 14],
  // Hubs sit on the north and west of the ring so their fronts face the camera
  // (it looks from the south-east); houses take the slots whose backs it sees.
  skills: [15], // north, beside the docks: the Skill Forge
  mcp: [1], // north-east: the Relay Tower
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

/**
 * The mountain's summit, flattened for the Agent Foundry (maintainer, 2026-09-02:
 * "the factory sits up there on the mountain, connected by the road"). The
 * highest tile of the snow cap; `PLOT_HALF.foundry` planes a terrace around it
 * and `SUMMIT_ROAD` climbs to it, so the works crown the island's backdrop and
 * a new agent walks the whole mountain road down into the village.
 */
export const SUMMIT_TILE: readonly [number, number] = [CENTER_TILE - 65, CENTER_TILE - 70];

/** Kit buildings that stand on a plot of their own instead of in the house ring. */
const KIT_ANCHOR: Partial<Record<KitPlace, readonly [number, number]>> = {
  foundry: SUMMIT_TILE,
};

/** Every kit place, ring-bound or not — the world's hub roster. */
export const KIT_PLACES: readonly KitPlace[] = ["plugins", "foundry", "skills", "mcp", "cli"];

/** Whether a place id names a World Kit hub (the ones a click opens). */
export function isKitPlace(id: string): id is KitPlace {
  return (KIT_PLACES as readonly string[]).includes(id);
}

/**
 * Where the island camera stands, as a unit vector on the ground: it looks
 * from the south-east (`worldCamera.ts`, CAMERA_YAW_DEG = 45). A front that
 * points along this vector faces the viewer; one that points against it
 * shows its back. `worldCamera.test.ts` pins the two files to each other.
 */
export const CAMERA_FROM: readonly [number, number] = [Math.SQRT1_2, Math.SQRT1_2];

/** Wrap an angle into (−π, π]. */
export function normalizeAngle(a: number): number {
  let d = a % (2 * Math.PI);
  if (d > Math.PI) d -= 2 * Math.PI;
  if (d <= -Math.PI) d += 2 * Math.PI;
  return d;
}

/** Whether a front heading (rotation about y, front = local +z) faces the camera at all. */
export function facesCamera(rotation: number): boolean {
  return Math.sin(rotation) * CAMERA_FROM[0] + Math.cos(rotation) * CAMERA_FROM[1] > -1e-9;
}

/**
 * A house's resting heading. Houses face the square — unless that turns their
 * back on the viewer, in which case they face the ring road instead: a door
 * the camera never sees is a house with its back turned (maintainer,
 * 2026-09-02). A house seen exactly in profile keeps the square.
 */
export function houseDefaultRotation(x: number, z: number): number {
  const toSquare = Math.atan2(-x, -z);
  return facesCamera(toSquare) ? toSquare : normalizeAngle(toSquare + Math.PI);
}

/** Where a kit building stands and which way it turns — the one source. */
export function kitPose(place: KitPlace): KitPose {
  const facing = KIT_FACING[place];
  const anchor = KIT_ANCHOR[place];
  if (anchor) {
    const [x, z] = tileToWorld(anchor[0], anchor[1]);
    return { x, z, rotation: facing ?? houseDefaultRotation(x, z) };
  }
  const slots = RING_KIT_SLOTS[place];
  if (!slots) throw new Error(`kit place ${place} has neither ring slots nor an anchor`);
  const pose = ringPose(slots);
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
  // The mine: a portal in a cliff on the mountain's south-eastern flank.
  mine: [CENTER_TILE - 32, CENTER_TILE - 60],
  // Kit buildings stand in the house ring (RING_KIT_SLOTS); the tile is the ring pose's.
  plugins: ringTile("plugins"),
  foundry: [SUMMIT_TILE[0], SUMMIT_TILE[1]],
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
  // The built island's pose, so a turned foundry delivers out of its turned portal.
  const p = cached?.content.kitPoses.foundry ?? kitPose("foundry");
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
  mine: [5, 4],
  plugins: [0, 0], // no flat plot of its own: it sits on the village plateau
  // The foundry's terrace: the summit planed flat, wide enough for the hall,
  // its conveyor and a rim of snow around the lot.
  foundry: [10, 8],
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
  mine: 4,
  plugins: PLATEAU_LEVEL,
  foundry: SUMMIT_LEVEL,
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

/** The cliff the mine's portal is cut into: a rock block north of the forecourt. */
const MINE_CLIFF = { dz: -8, hw: 8, hd: 3, level: 7 } as const;
/** The mine's branch road leaves the north spoke here and runs west to the forecourt. */
const MINE_ROAD = { fromX: CENTER_TILE - 1, z: CENTER_TILE - 60, length: 27 } as const;

/**
 * The mountain road to the Agent Foundry: three graded legs off the western
 * end of the mine's branch, up the flank and onto the summit terrace, arriving
 * at the foot of the works' conveyor. Straight legs only — `gradeRoad` widens
 * across its own axis, so a diagonal would lay its shoulders lengthwise.
 */
const SUMMIT_ROAD: ReadonlyArray<{
  from: [number, number];
  dir: [number, number];
  length: number;
}> = [
  { from: [CENTER_TILE - 37, CENTER_TILE - 60], dir: [-1, 0], length: 17 },
  { from: [CENTER_TILE - 54, CENTER_TILE - 60], dir: [0, -1], length: 5 },
  { from: [CENTER_TILE - 54, CENTER_TILE - 65], dir: [-1, 0], length: 6 },
];

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
  /** Dry savanna on the eastern lowland, between the highland and the cove. */
  savanna: { x: 0.64, z: 0.2, r: 0.3 },
  /** The wetland on the south-western coast. */
  marsh: { x: -0.62, z: 0.46, r: 0.2 },
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
  const inSavanna = regionT(nx, nz, REGIONS.savanna) < 1;
  const inMarsh = regionT(nx, nz, REGIONS.marsh) < 1;
  const inOrchard = regionT(nx, nz, REGIONS.orchard) < 0.85;
  const grain = fbm(nx * 6 + 31, nz * 6 + 17, ISLAND_SEED + 3);
  // A second, coarser grain decides the patchwork biomes: heather drifts, fields, gravel.
  const patchwork = fbm(nx * 4.2 + 5, nz * 4.2 + 9, ISLAND_SEED + 31);
  if (level === 6) {
    if (inMountain || inCape) return grain > 0.42 ? TileKind.rock : TileKind.scree;
    return patchwork > 0.5 ? TileKind.heath : TileKind.alpine;
  }
  if (level === 5) {
    if (inMountain || inCape) return grain > 0.55 ? TileKind.rock : grain > 0.3 ? TileKind.scree : TileKind.alpine;
    if (inHighland) return patchwork > 0.52 ? TileKind.heath : TileKind.alpine;
    return TileKind.meadow;
  }
  if (level === 1) return inMarsh ? TileKind.marsh : TileKind.sand;
  if (level === 2) {
    if (inCove) return TileKind.sand;
    if (inMarsh) return TileKind.marsh;
    if (inSavanna) return TileKind.dry;
    if (inOrchard) return patchwork > 0.45 ? TileKind.farm : TileKind.meadow;
    return grain > 0.6 ? TileKind.meadow : TileKind.grass;
  }
  // Levels 3 and 4: the rolling middle ground.
  if (inForest && grain > 0.3) return TileKind.forest;
  if (inSavanna && level === 3) return grain > 0.7 ? TileKind.meadow : TileKind.dry;
  if (inOrchard && level === 3) return patchwork > 0.42 ? TileKind.farm : TileKind.meadow;
  if (inMountain && level === 4 && grain > 0.6) return TileKind.scree;
  if (level === 4) {
    if (inHighland) return patchwork > 0.55 ? TileKind.heath : grain > 0.5 ? TileKind.alpine : TileKind.meadow;
    return grain > 0.45 ? TileKind.meadow : TileKind.grass;
  }
  return grain > 0.58 ? TileKind.meadow : TileKind.grass;
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

  // Pools in the marsh: sea-level water inside the wetland, ringed by reeds.
  for (let tz = 0; tz < map.size; tz++) {
    for (let tx = 0; tx < map.size; tx++) {
      const i = tileIndex(map, tx, tz);
      if (map.kind[i] !== TileKind.marsh) continue;
      const nx = (tx + 0.5 - C) / C;
      const nz = (tz + 0.5 - C) / C;
      if (fbm(nx * 16 + 2, nz * 16 + 6, ISLAND_SEED + 21) > 0.64) {
        map.kind[i] = TileKind.pool;
        map.level[i] = 0;
        map.blocked[i] = 1;
      }
    }
  }

  // The quarry: a gravel apron around the mine and the cliff its portal is cut into.
  const [mx, mz] = PLACE_TILES.mine;
  paintDisc(map, mx, mz, 0, 11, (i) => {
    if (map.kind[i] === TileKind.water || map.kind[i] === TileKind.snow) return;
    if (map.level[i] >= 7) return;
    paintLand(map, i, map.level[i] >= 4 ? TileKind.scree : TileKind.quarry, map.level[i]);
  });
  fillRect(map, mx, mz + MINE_CLIFF.dz, MINE_CLIFF.hw, MINE_CLIFF.hd, (i) => {
    if (map.kind[i] === TileKind.water) return;
    paintLand(map, i, TileKind.rock, MINE_CLIFF.level);
  });

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
            : id === "mine"
              ? TileKind.quarry
              : // The foundry's terrace is planed mountain, not village paving.
                id === "foundry"
                ? TileKind.scree
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
function gradeRoad(
  map: IslandMap,
  start: [number, number],
  dir: [number, number],
  length: number,
  startLevel: number,
): void {
  let prev = startLevel;
  for (let s = 0; s <= length; s++) {
    const cx = start[0] + dir[0] * s;
    const cz = start[1] + dir[1] * s;
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
  for (const spoke of SPOKES) {
    const from = PLAZA_RADIUS_TILES - 1;
    gradeRoad(map, [C + spoke.dir[0] * from, C + spoke.dir[1] * from], spoke.dir, spoke.toTiles - from, PLATEAU_LEVEL);
  }
  // The mine's branch: off the north spoke, west to the quarry, at the spoke's level there.
  const junction = map.level[tileIndex(map, MINE_ROAD.fromX + 1, MINE_ROAD.z)];
  gradeRoad(map, [MINE_ROAD.fromX, MINE_ROAD.z], [-1, 0], MINE_ROAD.length, junction);
  // On up the mountain to the foundry: each leg starts at the level the last
  // one reached, so the climb never breaks a walker's one-level step rule.
  let climb = map.level[tileIndex(map, SUMMIT_ROAD[0].from[0] + 1, SUMMIT_ROAD[0].from[1])];
  for (const leg of SUMMIT_ROAD) {
    gradeRoad(map, leg.from, leg.dir, leg.length, climb);
    const end: [number, number] = [
      leg.from[0] + leg.dir[0] * leg.length,
      leg.from[1] + leg.dir[1] * leg.length,
    ];
    if (inBounds(map, end[0], end[1])) climb = map.level[tileIndex(map, end[0], end[1])];
  }

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

  // The mine's portal and its rail head block walking; the forecourt stays open.
  const [mx, mz] = PLACE_TILES.mine;
  fillRect(map, mx, mz - 3, 3, 1, (i) => (map.blocked[i] = 1));

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
    const [x, z] = ringSlotWorld(k);
    const seed = hash2(k, 7, ISLAND_SEED);
    const rotation = houseDefaultRotation(x, z);
    houses.push({
      slot: k,
      x,
      z,
      w: 3,
      d: 2,
      rotation,
      defaultRotation: rotation,
      variant: variants[k % variants.length],
      seed,
    });
  }
  return houses;
}

/**
 * Clearance a walker keeps from a wall, in metres: the footprint is stamped
 * this much wider so a figure's shoulder never clips a house corner.
 */
export const WALL_MARGIN_M = 0.5;

/**
 * Stamp `value` over every tile a rotated rectangle covers — `w` × `d` metres
 * at (x, z), turned by `rotation` about y (three.js convention: local +z is
 * the front), grown by `margin` on every side. Sampled every half metre so no
 * tile inside the footprint is skipped.
 */
export function stampFootprint(
  layer: Uint8Array,
  map: IslandMap,
  x: number,
  z: number,
  w: number,
  d: number,
  rotation: number,
  margin: number,
  value: 0 | 1,
): void {
  const hw = w / 2 + margin;
  const hd = d / 2 + margin;
  const cos = Math.cos(rotation);
  const sin = Math.sin(rotation);
  for (let lx = -hw; lx <= hw + 1e-9; lx += 0.5) {
    for (let lz = -hd; lz <= hd + 1e-9; lz += 0.5) {
      const wx = x + lx * cos + lz * sin;
      const wz = z - lx * sin + lz * cos;
      const [tx, tz] = worldToTile(wx, wz);
      if (inBounds(map, tx, tz)) layer[tileIndex(map, tx, tz)] = value;
    }
  }
}

/** Block an axis-aligned rectangle given in world metres (x0..x1, z0..z1). */
function blockRectM(map: IslandMap, x0: number, z0: number, x1: number, z1: number): void {
  const [tx0, tz0] = worldToTile(Math.min(x0, x1), Math.min(z0, z1));
  const [tx1, tz1] = worldToTile(Math.max(x0, x1) - 1e-6, Math.max(z0, z1) - 1e-6);
  for (let tz = tz0; tz <= tz1; tz++) {
    for (let tx = tx0; tx <= tx1; tx++) {
      if (inBounds(map, tx, tz)) map.blocked[tileIndex(map, tx, tz)] = 1;
    }
  }
}

/** Block the single tile under a post (a lamp, a pillar, a mast) — never a dock plank. */
function blockPost(map: IslandMap, x: number, z: number): void {
  const [tx, tz] = worldToTile(x, z);
  if (!inBounds(map, tx, tz)) return;
  const i = tileIndex(map, tx, tz);
  if (map.kind[i] === TileKind.dock) return;
  map.blocked[i] = 1;
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
  // Lamps around the square's rim and along the ring road, between the gates.
  const ringLamps = (radiusTiles: number, count: number, offset: number) => {
    for (let k = 0; k < count; k++) {
      const angle = ((k + offset) / count) * Math.PI * 2;
      // Never in a gate or on a spoke.
      const toCardinal = Math.abs(((angle + Math.PI / 4) % (Math.PI / 2)) - Math.PI / 4);
      if (toCardinal < 0.12) continue;
      const x = Math.sin(angle) * radiusTiles * TILE_M;
      const z = -Math.cos(angle) * radiusTiles * TILE_M;
      const [tx, tz] = worldToTile(x, z);
      if (!inBounds(map, tx, tz) || map.blocked[tileIndex(map, tx, tz)]) continue;
      lamps.push({ x, z, y: groundY(map, x, z), rotation: 0 });
    }
  };
  ringLamps(PLAZA_RADIUS_TILES + 2.2, 16, 0.5);
  ringLamps(RING_ROAD_TILES + 1.8, 16, 0.5);
  // Lanterns along the dock and the mine's road.
  for (let tz = DOCK_TILES.from + 2; tz <= DOCK_TILES.to; tz += 4) {
    const [x, z] = tileToWorld(CENTER_TILE + DOCK_TILES.halfWidth, tz);
    lamps.push({ x: x + 0.7, z, y: DOCK_Y, rotation: 0 });
  }
  for (let s = 4; s < MINE_ROAD.length; s += 8) {
    const [x, z] = tileToWorld(MINE_ROAD.fromX - s, MINE_ROAD.z + 2);
    lamps.push({ x, z, y: groundY(map, x, z), rotation: 0 });
  }
  return { hedges, lamps };
}

/** Eight poles around the square that carry the festoon lights over it. */
function placeFestoonPoles(map: IslandMap): Post[] {
  const poles: Post[] = [];
  const r = (PLAZA_RADIUS_TILES - 1.5) * TILE_M;
  for (let k = 0; k < 8; k++) {
    const angle = ((k + 0.5) / 8) * Math.PI * 2;
    const x = Math.sin(angle) * r;
    const z = -Math.cos(angle) * r;
    poles.push({ x, z, y: groundY(map, x, z), rotation: angle });
    const [tx, tz] = worldToTile(x, z);
    if (inBounds(map, tx, tz)) map.blocked[tileIndex(map, tx, tz)] = 1;
  }
  return poles;
}

/** The campfire: the first free sand tile near the lagoon's western shore. */
function placeCampfire(map: IslandMap): Post {
  const C = CENTER_TILE;
  const cx = Math.round(C + REGIONS.lagoon.x * C - REGIONS.lagoon.r * C - 9);
  const cz = Math.round(C + REGIONS.lagoon.z * C + 4);
  for (let r = 0; r < 12; r++) {
    for (let dz = -r; dz <= r; dz++) {
      for (let dx = -r; dx <= r; dx++) {
        const tx = cx + dx;
        const tz = cz + dz;
        if (!inBounds(map, tx, tz)) continue;
        let ok = true;
        fillRect(map, tx, tz, 1, 1, (i) => {
          if (map.kind[i] !== TileKind.sand || map.blocked[i]) ok = false;
        });
        if (!ok) continue;
        fillRect(map, tx, tz, 1, 1, (i) => (map.blocked[i] = 1));
        const [x, z] = tileToWorld(tx, tz);
        return { x, z, y: groundY(map, x, z), rotation: 0 };
      }
    }
  }
  const [x, z] = tileToWorld(cx, cz);
  return { x, z, y: groundY(map, x, z), rotation: 0 };
}

/** Reeds on the marsh, thickest at the pools' edges. */
function placeReeds(map: IslandMap): Post[] {
  const reeds: Post[] = [];
  for (let tz = 1; tz < map.size - 1; tz++) {
    for (let tx = 1; tx < map.size - 1; tx++) {
      const i = tileIndex(map, tx, tz);
      if (map.kind[i] !== TileKind.marsh || map.blocked[i]) continue;
      let pool = false;
      for (const [dx, dz] of [
        [1, 0],
        [-1, 0],
        [0, 1],
        [0, -1],
      ] as const) {
        if (map.kind[tileIndex(map, tx + dx, tz + dz)] === TileKind.pool) pool = true;
      }
      const density = pool ? 0.8 : 0.28;
      if (hash2(tx, tz, ISLAND_SEED + 61) > density) continue;
      const [x, z] = tileToWorld(tx, tz);
      reeds.push({
        x: x + (hash2(tx, tz, 62) - 0.5) * 1.4,
        z: z + (hash2(tx, tz, 63) - 0.5) * 1.4,
        y: LEVEL_Y[map.level[i]],
        rotation: hash2(tx, tz, 64) * Math.PI * 2,
      });
    }
  }
  return reeds;
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
  if (k === TileKind.heath) return { kind: "pine", density: 0.025 };
  if (k === TileKind.dry) return { kind: "round", density: 0.018 };
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
      if (k === TileKind.water || k === TileKind.pool || k === TileKind.path || k === TileKind.plaza || k === TileKind.dock) continue;
      if (Math.hypot(tx + 0.5 - C, tz + 0.5 - C) < HEDGE_RING_TILES + 4) continue;
      let density: number;
      if (k === TileKind.rock || k === TileKind.snow) density = 0.05;
      else if (k === TileKind.quarry) density = 0.09;
      else if (k === TileKind.scree) density = 0.06;
      else if (k === TileKind.alpine || k === TileKind.heath) density = 0.035;
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

/**
 * How far in front of each kit building a visitor stands, in metres: in front
 * of the docks' bays, at the foot of the foundry's conveyor ramp, at the
 * others' doors.
 */
export const KIT_STAND_AHEAD_M: Record<KitPlace, number> = {
  plugins: 7.5,
  foundry: 13.5,
  skills: 7.5,
  mcp: 7.0,
  cli: 7.5,
};

/** A stand point in front of a kit building at `pose`, facing it. */
function kitPlace(id: KitPlace, pose: KitPose = kitPose(id)): Place {
  const ahead = KIT_STAND_AHEAD_M[id];
  const tile = worldToTile(pose.x + Math.sin(pose.rotation) * ahead, pose.z + Math.cos(pose.rotation) * ahead);
  return { id, tile: PLACE_TILES[id], standTile: tile, facing: normalizeAngle(pose.rotation + Math.PI) };
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
    // On the mine's road at the edge of the forecourt, facing the portal (west).
    mine: place("mine", [PLACE_TILES.mine[0] + 7, PLACE_TILES.mine[1]], -Math.PI / 2),
    // In front of the kit buildings (KIT_STAND_AHEAD_M), facing them.
    plugins: kitPlace("plugins"),
    foundry: kitPlace("foundry"),
    skills: kitPlace("skills"),
    mcp: kitPlace("mcp"),
    cli: kitPlace("cli"),
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

/**
 * Radius (metres) of the Quest Board's plinth and ring bench in the middle of
 * the square, plus the walker's clearance: nobody walks through a bench.
 */
export const SQUARE_CENTRE_BLOCK_M = 6.0;
/** The long table with its two benches on the square's south side, world metres. */
export const LONG_TABLE_RECT_M = { x0: -5.2, z0: 6.6, x1: 5.2, z1: 10.4 } as const;

/** The Quest Board with its ring bench and the long table block the middle of the square. */
function blockSquareFurniture(map: IslandMap): void {
  const C = CENTER_TILE;
  paintDisc(map, C, C, 0, SQUARE_CENTRE_BLOCK_M / TILE_M, (i) => (map.blocked[i] = 1));
  const t = LONG_TABLE_RECT_M;
  blockRectM(map, t.x0, t.z0, t.x1, t.z1);
}

/**
 * What the landmark components put on the ground beside their blocked
 * building footprints — the hub's wings, colonnade, planters, pool and flag
 * masts, the harbor's kiosk and gate pillars, the lighthouse keeper's hut.
 * The numbers are the components' own (Village.tsx `Hub`, Landmarks.tsx
 * `Harbor` / `Lighthouse`), in each building's local metres.
 */
function blockLandmarkFurniture(map: IslandMap): void {
  const [hx, hz] = tileToWorld(...PLACE_TILES.hub);
  // Wings, 7 × 8 m at ±15.5 m, one metre back.
  for (const ox of [-15.5, 15.5]) blockRectM(map, hx + ox - 3.5, hz - 3, hx + ox + 3.5, hz + 5);
  for (const px of [-10, -6, -2, 2, 6, 10]) blockPost(map, hx + px, hz + 7.4); // colonnade
  for (const px of [-13, 13]) blockRectM(map, hx + px - 1.2, hz + 6.3, hx + px + 1.2, hz + 8.7); // planters
  blockRectM(map, hx - 4.2, hz + 12.1, hx + 4.2, hz + 14.3); // the reflecting pool
  for (const px of [-6.2, 6.2]) blockPost(map, hx + px, hz + 13.2); // flag masts

  const [bx, bz] = tileToWorld(...PLACE_TILES.harbor);
  blockRectM(map, bx - 9, bz + 0.25, bx - 5, bz + 3.75); // harbor master's kiosk
  for (const px of [-3, 3]) blockPost(map, bx + px, bz + 8); // gate pillars

  const [lx, lz] = tileToWorld(...PLACE_TILES.lighthouse);
  const s = 1.3; // the lighthouse group's scale
  blockRectM(map, lx + (-5 - 2) * s, lz + (3 - 1.7) * s, lx + (-5 + 2) * s, lz + (3 + 1.7) * s); // keeper's hut
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
    blockedStatic: new Uint8Array(0), // filled once everything stands
  };
  buildTerrain(map);
  buildPlots(map);
  buildVillage(map);
  const houses = placeHouses();
  const kitPoses: Record<KitPlace, KitPose> = {
    plugins: kitPose("plugins"),
    foundry: kitPose("foundry"),
    skills: kitPose("skills"),
    mcp: kitPose("mcp"),
    cli: kitPose("cli"),
  };
  // Houses, kit halls and the foundry's belt block walking at their resting
  // headings for now, so trees and furniture keep clear of them; the final
  // headings are stamped again by `applyBuildingYaws` below.
  stampTurnables(map, map.blocked, houses, kitPoses, 1);
  blockSquareFurniture(map);
  blockLandmarkFurniture(map);
  const { hedges, lamps } = placeRingFurniture(map);
  // A hedge is a wall to a walker; a lamp post is a post.
  for (const h of hedges) stampFootprint(map.blocked, map, h.x, h.z, HEDGE_SEGMENT_M[0], HEDGE_SEGMENT_M[1], h.rotation, 0.3, 1);
  for (const l of lamps) blockPost(map, l.x, l.z);
  const { panels, greenhouses } = placeQuarterFurniture(map);
  const festoonPoles = placeFestoonPoles(map);
  const campfire = placeCampfire(map);
  const trees = placeTrees(map);
  const boulders = placeBoulders(map);
  const reeds = placeReeds(map);
  const places = buildPlaces(map);
  // Everything that never turns: the turnable buildings lifted out again.
  map.blockedStatic = map.blocked.slice();
  stampTurnables(map, map.blockedStatic, houses, kitPoses, 0);
  cached = {
    map,
    content: {
      places,
      houses,
      trees,
      boulders,
      reeds,
      festoonPoles,
      campfire,
      hedges,
      lamps,
      panels,
      greenhouses,
      kitPoses,
    },
  };
  applyBuildingYaws(cached, {});
  return cached;
}

/** Tests that need a fresh build. */
export function resetIslandCache(): void {
  cached = null;
}

// ---------------------------------------------------------------------------
// Turning buildings — the viewer's own headings over the designed ones
// ---------------------------------------------------------------------------

/** Stable id of a building a viewer may turn: a ring house by slot, a ring hub by place. */
export type BuildingId = `house:${number}` | `kit:${KitPlace}`;

export function houseId(slot: number): BuildingId {
  return `house:${slot}`;
}

export function kitId(place: KitPlace): BuildingId {
  return `kit:${place}`;
}

/** Centre of ring slot `slot` (0..15, clockwise from north), world metres. */
export function ringSlotWorld(slot: number, radiusTiles = HOUSE_RING_TILES): [number, number] {
  const angle = (slot / 16) * Math.PI * 2;
  const r = radiusTiles * TILE_M;
  return [Math.sin(angle) * r, -Math.cos(angle) * r];
}

/** The heading a building rests at — what "reset" returns to. */
export function defaultBuildingYaw(id: BuildingId): number {
  if (id.startsWith("kit:")) return kitPose(id.slice(4) as KitPlace).rotation;
  const [x, z] = ringSlotWorld(Number(id.slice(6)));
  return houseDefaultRotation(x, z);
}

/** Size of one hedge segment (along the ring × across), metres — `Village.tsx` draws it so. */
export const HEDGE_SEGMENT_M: readonly [number, number] = [2.3, 0.8];

/** Stamp the turnable buildings' footprints at their current headings into `layer`. */
function stampTurnables(
  map: IslandMap,
  layer: Uint8Array,
  houses: HousePlot[],
  kitPoses: Record<KitPlace, KitPose>,
  value: 0 | 1,
): void {
  for (const h of houses) {
    stampFootprint(layer, map, h.x, h.z, h.w * TILE_M, h.d * TILE_M, h.rotation, WALL_MARGIN_M, value);
  }
  for (const id of Object.keys(kitPoses) as KitPlace[]) {
    const p = kitPoses[id];
    const f = KIT_FOOTPRINT_TILES[id];
    stampFootprint(layer, map, p.x, p.z, f.w * TILE_M, f.d * TILE_M, p.rotation, WALL_MARGIN_M, value);
  }
  // The foundry's conveyor deck is furniture, not floor: it keeps trees off
  // and keeps strollers beside it. A newborn rides it on fixed waypoints, so
  // the block costs the entrance nothing.
  const belt = kitPoses.foundry;
  stampFootprint(
    layer,
    map,
    belt.x + Math.sin(belt.rotation) * FOUNDRY_RAMP_MID_M,
    belt.z + Math.cos(belt.rotation) * FOUNDRY_RAMP_MID_M,
    3 * TILE_M,
    3 * TILE_M,
    belt.rotation,
    WALL_MARGIN_M,
    value,
  );
}

/**
 * Turn the buildings a viewer has turned: `yaws` maps a BuildingId to a
 * heading (radians about y); anything absent rests at its default. Rewrites,
 * in place, every number that depends on a heading — the house and kit poses,
 * the blocked layer (from `blockedStatic`), the kit places' stand tiles and
 * facings — so the renderer, the walkers and the pathfinder all see the same
 * turned building. Pure over the island it is given; `buildingPoses.ts` is
 * the only caller besides `buildIsland` itself.
 */
export function applyBuildingYaws(island: Island, yaws: Readonly<Partial<Record<BuildingId, number>>>): void {
  const { map, content } = island;
  for (const h of content.houses) h.rotation = yaws[houseId(h.slot)] ?? h.defaultRotation;
  for (const id of Object.keys(content.kitPoses) as KitPlace[]) {
    content.kitPoses[id].rotation = yaws[kitId(id)] ?? kitPose(id).rotation;
    content.places[id] = kitPlace(id, content.kitPoses[id]);
  }
  map.blocked.set(map.blockedStatic);
  stampTurnables(map, map.blocked, content.houses, content.kitPoses, 1);
  // A stand tile stays reachable whatever turned over it — a place nobody can reach is a bug.
  for (const p of Object.values(content.places)) {
    const [tx, tz] = p.standTile;
    if (inBounds(map, tx, tz)) map.blocked[tileIndex(map, tx, tz)] = 0;
  }
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
    // From just outside the ring bench to just inside the garden beds.
    const r = 4 + rng() * (PLAZA_RADIUS_TILES - 5);
    const tx = Math.floor(CENTER_TILE + Math.cos(angle) * r);
    const tz = Math.floor(CENTER_TILE + Math.sin(angle) * r);
    if (isWalkable(map, tx, tz)) return [tx, tz];
  }
  return [CENTER_TILE + 4, CENTER_TILE + 4];
}
