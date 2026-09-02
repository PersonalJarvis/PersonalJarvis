/**
 * A figure recipe is everything that tells one agent's character apart from
 * the next — archetype, base, parts and a 16-cell palette — without a byte of
 * new geometry (docs/agent-society/character-pipeline.md §9.1). It is the
 * JSON the roster row will carry as `figure_json`; until the backend lands,
 * the sample roster carries it inline.
 *
 * The cell order is the contract's (`scripts/figures/contract.json`): the
 * sheet's top strip holds these sixteen colours, 8 px wide each, and the
 * runtime repaints exactly those cells to recolour a figure.
 */

export const PALETTE_CELLS = [
  "skin",
  "skin_shade",
  "hair",
  "eyes",
  "primary",
  "primary_shade",
  "secondary",
  "secondary_shade",
  "accent",
  "metal",
  "leather",
  "fur",
  "fur_shade",
  "shoes",
  "eye_white",
  "emissive",
] as const;

export type PaletteCell = (typeof PALETTE_CELLS)[number];
export type Palette = Record<PaletteCell, string>;

export type FigureArchetype = "biped" | "quadruped" | "spirit";

export interface FigureRecipe {
  contract: 1;
  archetype: FigureArchetype;
  /** Base id: "small" | "medium" | "large" for a biped; an animal id for a quadruped. */
  base: string;
  /** slot → part id; an empty object is a complete, plainly dressed figure. */
  parts: Record<string, string>;
  /** Cells to repaint; every cell missing here keeps the archetype default. */
  palette?: Partial<Palette>;
  /** Rendered height in metres; the archetype/base default when absent. */
  heightM?: number;
}

/** The biped's default look — the built sheet's own strip, for a natural first figure. */
export const BIPED_DEFAULT_PALETTE: Palette = {
  skin: "#f4b68f",
  skin_shade: "#d99a75",
  hair: "#9e5d47",
  eyes: "#1b2427",
  primary: "#096153",
  primary_shade: "#07483d",
  secondary: "#b16f51",
  secondary_shade: "#008c55",
  accent: "#e8c46b",
  metal: "#919da2",
  leather: "#9a5944",
  fur: "#b87556",
  fur_shade: "#8d4c39",
  shoes: "#4a2e22",
  eye_white: "#e6ecef",
  emissive: "#ffd166",
};

/** The cells a person edits by hand; the shades derive from them. */
export const EDITABLE_CELLS: readonly PaletteCell[] = [
  "skin",
  "hair",
  "primary",
  "secondary",
  "accent",
  "shoes",
];

export interface PalettePreset {
  id: string;
  /** i18n key under `society.presets` */
  labelKey: string;
  palette: Partial<Palette>;
}

/** Hand-picked looks; a preset fills the editable cells, the rest derives. */
export const PALETTE_PRESETS: readonly PalettePreset[] = [
  {
    id: "executive",
    labelKey: "executive",
    palette: {
      skin: "#f1c4a0",
      hair: "#3b2a20",
      primary: "#1f2a44",
      secondary: "#f2f2ee",
      accent: "#c9a227",
      shoes: "#1a1a1a",
      leather: "#2b2b2b",
      metal: "#c9a227",
    },
  },
  {
    id: "ranger",
    labelKey: "ranger",
    palette: {
      skin: "#f4b68f",
      hair: "#9e5d47",
      primary: "#096153",
      secondary: "#b16f51",
      accent: "#e8c46b",
      shoes: "#4a2e22",
    },
  },
  {
    id: "archivist",
    labelKey: "archivist",
    palette: {
      skin: "#e9c3a3",
      hair: "#6b6b6b",
      primary: "#5a7a4f",
      secondary: "#e8dcc4",
      accent: "#e8c46b",
      shoes: "#3a2a1e",
    },
  },
  {
    id: "scout",
    labelKey: "scout",
    palette: {
      skin: "#d8a37c",
      hair: "#2a1d15",
      primary: "#c05b3c",
      secondary: "#efe0cd",
      accent: "#8fd0a0",
      shoes: "#2b2b2b",
    },
  },
  {
    id: "mage",
    labelKey: "mage",
    palette: {
      skin: "#f5ba95",
      hair: "#252124",
      primary: "#4a4673",
      secondary: "#b25729",
      accent: "#54b56b",
      shoes: "#201e3c",
    },
  },
];

/** Darken a hex colour by `amount` (0..1) for the derived shade cells. */
export function shade(hex: string, amount: number): string {
  const [r, g, b] = hexToRgb(hex);
  const f = 1 - amount;
  return rgbToHex(r * f, g * f, b * f);
}

export function hexToRgb(hex: string): [number, number, number] {
  const v = hex.replace("#", "");
  const n = parseInt(v.length === 3 ? v.split("").map((c) => c + c).join("") : v, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function rgbToHex(r: number, g: number, b: number): string {
  const to = (x: number) => Math.round(Math.min(255, Math.max(0, x))).toString(16).padStart(2, "0");
  return `#${to(r)}${to(g)}${to(b)}`;
}

/**
 * The full 16-cell palette a recipe renders with: archetype defaults, the
 * recipe's cells on top, and the shade cells derived from their base cell
 * unless the recipe set them explicitly.
 */
export function resolvePalette(recipe: Pick<FigureRecipe, "palette"> | null | undefined): Palette {
  const explicit = recipe?.palette ?? {};
  const out: Palette = { ...BIPED_DEFAULT_PALETTE, ...explicit };
  if (!explicit.skin_shade && explicit.skin) out.skin_shade = shade(explicit.skin, 0.18);
  if (!explicit.primary_shade && explicit.primary) out.primary_shade = shade(explicit.primary, 0.22);
  if (!explicit.secondary_shade && explicit.secondary) {
    out.secondary_shade = shade(explicit.secondary, 0.22);
  }
  if (!explicit.fur_shade && explicit.fur) out.fur_shade = shade(explicit.fur, 0.2);
  return out;
}

/** A stable key for caching painted sheets and face crops per look. */
export function recipeKey(recipe: FigureRecipe): string {
  const palette = PALETTE_CELLS.map((c) => recipe.palette?.[c] ?? "").join(",");
  const parts = Object.entries(recipe.parts)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}=${v}`)
    .join(";");
  return `${recipe.archetype}/${recipe.base}|${parts}|${palette}|${recipe.heightM ?? ""}`;
}

/** The recipe a brand-new biped starts from: the default base, one preset, no parts. */
export function defaultRecipe(presetId = "ranger"): FigureRecipe {
  const preset = PALETTE_PRESETS.find((p) => p.id === presetId) ?? PALETTE_PRESETS[0];
  return { contract: 1, archetype: "biped", base: "medium", parts: {}, palette: { ...preset.palette } };
}

/** The three legacy roster colours (primary/secondary/accent) as recipe cells. */
export function paletteFromLegacy(p: { primary: string; secondary: string; accent: string }): Partial<Palette> {
  return { primary: p.primary, secondary: p.secondary, accent: p.accent };
}

/** A different but plausible look — for the "shuffle" button in the creator. */
export function shufflePalette(seed = Math.random()): Partial<Palette> {
  const pick = (list: readonly string[], k: number) => list[Math.floor(((seed * 9301 + k * 49297) % 233280) / 233280 * list.length)];
  return {
    skin: pick(["#f4b68f", "#e9c3a3", "#d8a37c", "#b87556", "#8d5b3f", "#f1c4a0"], 1),
    hair: pick(["#3b2a20", "#9e5d47", "#252124", "#6b6b6b", "#e0b989", "#c0392b"], 2),
    primary: pick(["#1f2a44", "#096153", "#5a7a4f", "#c05b3c", "#4a4673", "#7d3620", "#2f6f8f"], 3),
    secondary: pick(["#f2f2ee", "#b16f51", "#e8dcc4", "#efe0cd", "#b25729", "#8fd0a0"], 4),
    accent: pick(["#c9a227", "#e8c46b", "#8fd0a0", "#ffd166", "#54b56b", "#d22227"], 5),
    shoes: pick(["#1a1a1a", "#4a2e22", "#3a2a1e", "#2b2b2b", "#201e3c"], 6),
  };
}
