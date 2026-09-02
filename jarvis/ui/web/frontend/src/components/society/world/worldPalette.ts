/**
 * The world's OWN palette — MASTERPLAN §4.3: the island is a game inside the
 * app and never wears Ink & Paper. Every colour rendered inside the viewport
 * comes from here; the surrounding chrome keeps the app's theme tokens.
 *
 * Direction (maintainer, 2026-09-01, docs/agent-society/world-art-direction.md):
 * a bright pixel island in the warm light of a late afternoon — saturated,
 * friendly greens and blues, warm sand and stone — with a SOLARPUNK village on
 * it: white walls, glass, solar barrels, garden roofs, wood accents. Two
 * shades per terrain kind give the tile-art flicker under the pixel pass.
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
};

/** Water: the animated surface and its pixel highlights. */
export const WATER = {
  deep: "#2f7fc4",
  surface: "#44a0dd",
  ripple: "#8fd0f4",
  foam: "#d9f2ff",
};

/** Sky and light. NoToneMapping keeps these exact. */
export const SKY = {
  clear: "#a5dbff",
  hemiSky: "#d6ecff",
  hemiGround: "#7f9c5a",
  sun: "#fff1d6",
  /** Directional and hemisphere intensities are plain multipliers in three r155+ — no π. */
  sunIntensity: 0.8,
  hemiIntensity: 0.78,
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
  beacon: "#ffe08a",
  beaconCore: "#fff6d5",
  workshopRoof: "#d86a4a",
  archiveDome: "#5aa7c8",
  lighthouseStripe: "#e05a5a",
  metal: "#8b8f9c",
};

export const NATURE = {
  trunk: "#7a5230",
  canopyA: "#4faf49",
  canopyB: "#6cc35e",
  canopyLight: "#93da7c",
  bigCanopy: "#57b84f",
  bigCanopyLight: "#8fdc7a",
  hedge: "#3f8f3f",
  hedgeLight: "#57a94f",
  flower: "#ef8fb6",
  lampPost: "#5c5f6a",
  lampLight: "#ffe9b0",
  tableWood: "#c58d4f",
  bench: "#a9723a",
};

/** In-world labels (drawn as DOM over the canvas, in the world's own type). */
export const LABEL = {
  font: '"Pixelify Sans", "Space Grotesk", ui-sans-serif, sans-serif',
  ink: "#1f2a3a",
  chip: "rgba(255, 252, 245, 0.88)",
  chipRim: "rgba(31, 42, 58, 0.18)",
  shadow: "rgba(31, 42, 58, 0.35)",
};

/** Colours for the 2D minimap, one per tile kind (top shade 0). */
export function minimapColor(kind: TileKind): string {
  return TILE_COLORS[kind].top[0];
}
