/**
 * The wardrobe catalogue: office outfits with curated colourways, hair and
 * skin swatches, and the pure recipe edits the wardrobe panel applies.
 *
 * An outfit's colours live in the recipe itself (palette primary = main
 * garment, secondary = trousers, accent = tie / trim, shoes; `inner` = the
 * shirt under a jacket), so a dressed figure looks the same in the office,
 * the creator preview and the appearance editor.
 */
import type { FigureRecipe } from "../figures/figureRecipe";
import { OUTFITS, type Outfit } from "./outfitCatalog";
import { hashString, SKIN_TONES, toyLookFor, type Eyewear, type HairStyle, type OutfitId } from "./toyFigureModel";

export { OUTFITS };
export type { Colourway, Outfit } from "./outfitCatalog";

export const HAIR_COLOURS = ["#1d1d24", "#2a1d15", "#4a3020", "#6b4226", "#9e5d47", "#c0392b", "#d9b36c", "#e8dcc0", "#8a8d93", "#d6d6d6"] as const;

export { SKIN_TONES };

export function outfitById(id: OutfitId): Outfit {
  return OUTFITS.find((o) => o.id === id) ?? OUTFITS[OUTFITS.length - 1];
}

/**
 * Dress a recipe in an outfit and one of its colourways. The current hair is
 * pinned explicitly, because a hair style derived from the palette would
 * otherwise change with the new colours.
 */
export function dressIn(recipe: FigureRecipe, outfit: OutfitId, colourwayIndex = 0): FigureRecipe {
  const look = toyLookFor(recipe, "");
  const ways = outfitById(outfit).colourways;
  const way = ways[((colourwayIndex % ways.length) + ways.length) % ways.length];
  return {
    ...recipe,
    outfit,
    inner: way.inner,
    hairStyle: look.hairStyle,
    palette: { ...recipe.palette, primary: way.primary, secondary: way.secondary, accent: way.accent, shoes: way.shoes },
  };
}

/** The colourway index the recipe currently wears, or -1 after a manual colour change. */
export function colourwayIndexOf(recipe: FigureRecipe): number {
  const look = toyLookFor(recipe, "");
  const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();
  return outfitById(look.outfit).colourways.findIndex((w) =>
    same(w.primary, look.shirt) && same(w.secondary, look.pants) && same(w.accent, look.shirtAccent)
    && same(w.inner, look.inner) && same(w.shoes, look.shoes));
}

/**
 * Write the look a recipe currently shows into its own fields (outfit,
 * colours, hair), so a later edit changes only what was edited. Without this
 * an undressed recipe's default office outfit — derived from the palette —
 * would change the moment someone picks a new skin tone.
 */
export function pinLook(recipe: FigureRecipe): FigureRecipe {
  const look = toyLookFor(recipe, "");
  if (recipe.outfit === look.outfit) return { ...recipe, hairStyle: look.hairStyle };
  return {
    ...recipe,
    outfit: look.outfit,
    inner: look.inner,
    hairStyle: look.hairStyle,
    palette: { ...recipe.palette, primary: look.shirt, secondary: look.pants, accent: look.shirtAccent, shoes: look.shoes },
  };
}

// Hair style and eyewear do not feed the default outfit's hash, so they need no pinning.
export function withHair(recipe: FigureRecipe, hairStyle: HairStyle): FigureRecipe {
  return { ...recipe, hairStyle };
}

export function withHairColour(recipe: FigureRecipe, hair: string): FigureRecipe {
  const pinned = pinLook(recipe);
  return { ...pinned, palette: { ...pinned.palette, hair } };
}

export function withSkin(recipe: FigureRecipe, skin: string): FigureRecipe {
  const pinned = pinLook(recipe);
  return { ...pinned, palette: { ...pinned.palette, skin } };
}

export function withEyewear(recipe: FigureRecipe, eyewear: Eyewear): FigureRecipe {
  return { ...recipe, eyewear };
}

/** A complete random office look: outfit, colourway, hair, hair colour and eyewear. */
export function randomLook(recipe: FigureRecipe, seed: string): FigureRecipe {
  const h = hashString(seed);
  const at = <T,>(list: readonly T[], salt: number): T => list[(Math.imul(h ^ salt, 0x9e3779b1) >>> 0) % list.length];
  const outfit = at(OUTFITS, 0x11);
  const hairStyles: readonly HairStyle[] = ["short", "slick", "sidepart", "buzz", "curly", "long", "ponytail", "bun", "spiky"];
  const dressed = dressIn(recipe, outfit.id, (h >>> 8) % outfit.colourways.length);
  const eyewear = at<Eyewear>(["none", "none", "none", "glasses", "shades"], 0x33);
  return { ...dressed, hairStyle: at(hairStyles, 0x22), eyewear, palette: { ...dressed.palette, hair: at(HAIR_COLOURS, 0x44) } };
}
