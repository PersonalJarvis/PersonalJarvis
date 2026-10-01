/**
 * The person's own character in the office: a name and a figure recipe.
 *
 * Stored per browser profile (localStorage) — it is a presentation choice, not
 * roster data, so it never touches society.db. Every read and write tolerates
 * a missing or blocked storage and falls back to the default look.
 */
import { CATALOG } from "../figures/figureRegistry";
import type { FigureRecipe } from "../figures/figureRecipe";
import { HAIR_STYLES, toyLookFor, type HairStyle, type ToyLook } from "./toyFigureModel";

const STORAGE_KEY = "jarvis.office.player.v1";

export interface PlayerProfile {
  name: string;
  recipe: FigureRecipe;
  /** The toy figure's hair; absent means the stable default from the player's identity. */
  hairStyle?: HairStyle;
}

export { HAIR_STYLES };

/** The person's recipe as the wardrobe edits it: an undressed character wears the T-shirt. */
export function playerRecipe(profile: PlayerProfile): FigureRecipe {
  const recipe = { ...profile.recipe, outfit: profile.recipe.outfit ?? "tee" };
  return profile.hairStyle ? { ...recipe, hairStyle: profile.hairStyle } : recipe;
}

/** The look the office draws for the person. */
export function playerLook(profile: PlayerProfile): ToyLook {
  // Agents get a default office outfit; the person keeps the T-shirt until they pick one.
  return toyLookFor(playerRecipe(profile), "office-player");
}

/** Bodies offered in the wardrobe: two-legged, first-party, not reserved. */
export const WARDROBE_BASES: readonly string[] = CATALOG.bases
  .filter((b) => b.archetype === "biped" && !b.styles.includes("custom"))
  .map((b) => b.base);

export function defaultProfile(): PlayerProfile {
  const base = WARDROBE_BASES.includes("toon") ? "toon" : WARDROBE_BASES[0] ?? "chibi";
  return {
    name: "",
    recipe: { contract: 1, archetype: "biped", base, parts: {}, palette: { primary: "#3f9d5a", secondary: "#2b4a8b", hair: "#6b3f2a", shoes: "#f2f2ee" } },
  };
}

function isRecipe(value: unknown): value is FigureRecipe {
  const r = value as Partial<FigureRecipe> | null;
  return !!r && r.contract === 1 && r.archetype === "biped" && typeof r.base === "string" && WARDROBE_BASES.includes(r.base)
    && typeof r.parts === "object" && r.parts !== null;
}

export function loadProfile(): PlayerProfile {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultProfile();
    const parsed = JSON.parse(raw) as Partial<PlayerProfile>;
    const fallback = defaultProfile();
    return {
      name: typeof parsed.name === "string" ? parsed.name.slice(0, 40) : fallback.name,
      recipe: isRecipe(parsed.recipe) ? parsed.recipe : fallback.recipe,
      hairStyle: HAIR_STYLES.includes(parsed.hairStyle as HairStyle) ? parsed.hairStyle : undefined,
    };
  } catch (error) {
    // Blocked storage (private window, thumbnail capture): the default look is the honest answer.
    console.debug("Office player profile unavailable", error);
    return defaultProfile();
  }
}

export function saveProfile(profile: PlayerProfile): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(profile));
  } catch (error) {
    console.debug("Office player profile not saved", error);
  }
}

export function withBase(profile: PlayerProfile, base: string): PlayerProfile {
  return { ...profile, recipe: { ...profile.recipe, base, parts: {} } };
}
