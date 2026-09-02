/**
 * The only module that names figure files. Everything else asks it for the
 * asset behind a recipe (docs/agent-society/character-pipeline.md §9.2).
 *
 * Every GLB here is produced by `scripts/figures/build_figures.py` and
 * validated by `scripts/ci/check_society_figures.py`; importing it through
 * Vite yields a fingerprinted URL, so a rebuilt figure never serves stale.
 */
import bipedMediumUrl from "@/assets/society/figures/biped-medium.glb";

import type { FigureArchetype, FigureRecipe } from "./figureRecipe";

export interface FigureAsset {
  url: string;
  archetype: FigureArchetype;
  base: string;
  /** Rendered height when the recipe names none. */
  defaultHeightM: number;
}

export const FIGURE_BASES: Readonly<Record<string, FigureAsset>> = {
  "biped/medium": {
    url: bipedMediumUrl,
    archetype: "biped",
    base: "medium",
    defaultHeightM: 1.75,
  },
};

/** Bases a person can pick today, per archetype — the creator disables the rest. */
export const AVAILABLE_BASES: Readonly<Record<FigureArchetype, readonly string[]>> = {
  biped: ["medium"],
  quadruped: [],
  spirit: [],
};

export function figureAssetFor(recipe: Pick<FigureRecipe, "archetype" | "base">): FigureAsset | null {
  return FIGURE_BASES[`${recipe.archetype}/${recipe.base}`] ?? null;
}

/** Every asset URL, for preloading the moment the section opens. */
export const FIGURE_URLS: readonly string[] = Object.values(FIGURE_BASES).map((a) => a.url);
