/**
 * The world's OWN palette — MASTERPLAN §4.3: the island is a game inside the
 * app and never wears Ink & Paper. Every colour rendered inside the viewport
 * comes from here; the surrounding chrome keeps the app's theme tokens.
 *
 * Direction (maintainer, 2026-09-01/02, docs/agent-society/world-art-direction.md):
 * a bright island in the warm light of a late afternoon — saturated, friendly
 * greens and blues, warm sand and stone — composed of biomes at different
 * heights (beach, meadows, forest, alpine, rock, snow) with a SOLARPUNK
 * village on its central plateau: white walls, glass, solar barrels, garden
 * roofs, wood accents. Two shades per terrain kind give the tile-art flicker
 * under the pixel pass; the level tint in `terrainGeometry.ts` lightens the
 * high ground a little, the way distance haze does.
 */
import { TileKind } from "./islandLayout";

export interface TileShades {
  /** The two top shades a tile alternates between. */
  top: [string, string];
  /** The cliff/side face colour where the tile steps down. */
  side: string;
}

export const TILE_COLORS: Record<TileKind, TileShades> = {
  [TileKind.water]: { top: ["#3d8fd1", "#3d8fd1"], side: "#2c6fa8" },
  [TileKind.sand]: { top: ["#f3e2ad", "#ead597"], side: "#d5bf86" },
  [TileKind.grass]: { top: ["#7ccb5c", "#6fbe51"], side: "#8e6a44" },
  [TileKind.meadow]: { top: ["#97d46c", "#8ac860"], side: "#8e6a44" },
  [TileKind.rock]: { top: ["#a8a7b3", "#9a99a6"], side: "#6f6e7c" },
  [TileKind.plaza]: { top: ["#ece1cf", "#e2d5c0"], side: "#b8a58a" },
  [TileKind.path]: { top: ["#dcc9a5", "#d0bd97"], side: "#a48c66" },
  [TileKind.garden]: { top: ["#b7dc6a", "#e18db1"], side: "#8e6a44" },
  [TileKind.dock]: { top: ["#b6853f", "#a87634"], side: "#7d5623" },
  [TileKind.forest]: { top: ["#4f9e47", "#47923f"], side: "#6d5236" },
  [TileKind.alpine]: { top: ["#bcd97c", "#aecf6f"], side: "#7c6a4c" },
  [TileKind.snow]: { top: ["#f6f9fc", "#e9eff6"], side: "#8f92a3" },
  [TileKind.heath]: { top: ["#b48ec6", "#a583b8"], side: "#7c6a4c" },
  [TileKind.dry]: { top: ["#dcd07e", "#cfc370"], side: "#a08a55" },
  [TileKind.farm]: { top: ["#bfb06a", "#9ac45e"], side: "#8e6a44" },
  [TileKind.marsh]: { top: ["#74b07e", "#67a271"], side: "#4c5e3c" },
  [TileKind.scree]: { top: ["#918e88", "#85827b"], side: "#6f6e7c" },
  [TileKind.quarry]: { top: ["#a2988a", "#948a7c"], side: "#6f6e7c" },
  [TileKind.pool]: { top: ["#4fa2c6", "#4a9bbf"], side: "#2c6fa8" },
};

/** Water: the animated surface, from the turquoise shallows to the open sea. */
export const WATER = {
  abyss: "#1e5c9a",
  deep: "#2f7fc4",
  surface: "#44a0dd",
  shallow: "#72d3e2",
  ripple: "#9fdcf6",
  foam: "#e4f6ff",
};

/** Sky and light. NoToneMapping keeps these exact. */
export const SKY = {
  clear: "#a5dbff",
  /** The sea's own highlight: the sun's colour on a wave face. */
  seaGlint: "#fff6df",
  hemiSky: "#d6ecff",
  hemiGround: "#7f9c5a",
  sun: "#fff1d6",
  /**
   * three r155+ lights are physical: Lambert/Toon divide irradiance by π, so a
   * directional or hemisphere intensity of π lights a surface to exactly its
   * colour. Lit horizontal ground lands just under 1.0 with these two; shade
   * keeps ~55 % — the Clash-of-Clans contrast without clipping white walls.
   */
  sunIntensity: 0.72 * Math.PI,
  hemiIntensity: 0.62 * Math.PI,
  /** Direction the sun shines FROM (unit-ish vector; warm afternoon, south-west). */
  sunFrom: [-60, 90, 45] as const,
};

/** The solarpunk village. */
export const BUILDING = {
  wall: "#f7f3ea",
  wallShade: "#e6e0d2",
  trim: "#c9c2b2",
  glass: "#8ed2f0",
  glassEmissive: "#4fa8d6",
  solar: "#26375a",
  solarLine: "#3e5f95",
  gardenRoof: "#6fbf55",
  gardenRoofBush: "#4c9c3d",
  wood: "#b57f45",
  woodDark: "#8c5e2f",
  door: "#e0893b",
  hubAccent: "#2f6f8f",
  hubGlass: "#bfe7ff",
  /** The hub's forecourt pool: still water, lit from within. */
  pool: "#86d9ea",
  beacon: "#ffe08a",
  beaconCore: "#fff6d5",
  workshopRoof: "#d86a4a",
  archiveDome: "#5aa7c8",
  lighthouseStripe: "#e05a5a",
  metal: "#8b8f9c",
  buoy: "#e8563f",
  /** The mine: the dark of the tunnel, the ore in the cart, the rails. */
  mineDark: "#10131a",
  ore: "#e8bd4c",
  rail: "#6d6f78",
};

export const NATURE = {
  trunk: "#7a5230",
  canopyA: "#4faf49",
  canopyB: "#6cc35e",
  canopyLight: "#93da7c",
  bigCanopy: "#57b84f",
  bigCanopyLight: "#8fdc7a",
  /** Conifers of the forest and the alpine slopes. */
  pineA: "#2f7f45",
  pineB: "#3b9452",
  pineLight: "#5aae64",
  /** Palms of the cove. */
  palmTrunk: "#a8794a",
  palmLeaf: "#4fb254",
  palmLeafLight: "#7ccf6c",
  /** Boulders: two greys, warm and cool. */
  boulderA: "#a19fab",
  boulderB: "#7e7c8a",
  hedge: "#3f8f3f",
  hedgeLight: "#57a94f",
  flower: "#ef8fb6",
  lampPost: "#5c5f6a",
  lampLight: "#ffe9b0",
  /** The warm pool a lamp throws on the ground, and the festoon bulbs over the square. */
  lampGlow: "#ffd68a",
  bulb: "#fff3c8",
  reed: "#7aa95e",
  reedHead: "#a8834b",
  fire: "#ff8f3a",
  fireCore: "#ffe37a",
  ember: "#ff5a2c",
  tableWood: "#c58d4f",
  bench: "#a9723a",
};

/**
 * The retirement ceremony (`retirement.ts`): the muzzle flash, the stretcher
 * the bearers carry, and the mark the body leaves on the ground. Its own
 * block because none of it belongs to a building or to the landscape.
 */
export const CEREMONY = {
  /** The flash at the barrel — hot white with a warm edge. */
  muzzle: "#ffe9a8",
  /** The stretcher's poles. */
  stretcherPole: "#6b4a2a",
  /** The canvas slung between them. */
  stretcherCloth: "#c9c0ac",
  /** The bearers' work clothes. */
  bearerCloth: "#4a4f5c",
  bearerTrim: "#2f333c",
  bearerLamp: "#ffd98a",
  /** What is left on the ground where the body fell. Dark, not lurid. */
  stain: "#4a1418",
};

/**
 * The signal arcing between two agents who are too far apart to talk.
 *
 * Deliberately NOT the sender's accent: half the roster's accents are greens
 * and blues, and the island is a green field beside a blue sea, so a call in
 * an agent's own colour is invisible exactly when it matters. Identity is
 * carried by the bubbles at both ends; the arc only has to READ.
 */
export const SIGNAL = {
  /** The beads: warm and near-white, the one hue nothing on the island wears. */
  bead: "#fff1b8",
  /** The ping at each end, a shade deeper so the two do not merge. */
  ping: "#ffb703",
  /** A dark backing behind each bead, so the warm core has an edge anywhere. */
  rim: "#2a2118",
};

/** In-world labels (drawn as DOM over the canvas, in the world's own type). */
export const LABEL = {
  font: '"Pixelify Sans", "Inter Variable", "Inter", ui-sans-serif, sans-serif',
  ink: "#1f2a3a",
  chip: "rgba(255, 252, 245, 0.88)",
  chipRim: "rgba(31, 42, 58, 0.18)",
  shadow: "rgba(31, 42, 58, 0.35)",
};

/** Colours for the 2D minimap, one per tile kind (top shade 0). */
export function minimapColor(kind: TileKind): string {
  return TILE_COLORS[kind].top[0];
}

/** A hex colour scaled by `factor` (clamped), for the minimap's height shading. */
export function shadeHex(hex: string, factor: number): string {
  const n = parseInt(hex.slice(1), 16);
  const ch = (shift: number) => Math.max(0, Math.min(255, Math.round(((n >> shift) & 255) * factor)));
  return `rgb(${ch(16)},${ch(8)},${ch(0)})`;
}
