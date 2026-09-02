/**
 * Load everything one recipe needs — the base GLB and each worn part — through
 * drei's cache, so thirty walkers wearing the same helmet parse it once.
 * Suspends until every file is in; callers wrap it in <Suspense>.
 */
import { useGLTF } from "@react-three/drei";

import type { LoadedGltf } from "./assembleFigure";
import type { FigureRecipe } from "./figureRecipe";
import { figureAssetFor, partAssetsFor, type CatalogPart } from "./figureRegistry";

export interface LoadedFigureAssets {
  base: LoadedGltf;
  parts: Array<{ gltf: LoadedGltf; part: CatalogPart }>;
  defaultHeightM: number;
}

export function figureUrls(recipe: FigureRecipe): { base: string; parts: ReturnType<typeof partAssetsFor> } | null {
  const asset = figureAssetFor(recipe);
  if (!asset) return null;
  return { base: asset.url, parts: partAssetsFor(recipe) };
}

/** Suspends. Call only when `figureAssetFor(recipe)` is non-null. */
export function useFigureAssets(recipe: FigureRecipe): LoadedFigureAssets {
  const asset = figureAssetFor(recipe);
  const parts = partAssetsFor(recipe);
  const urls = [asset?.url ?? "", ...parts.map((p) => p.url)].filter(Boolean);
  const loaded = useGLTF(urls) as unknown as LoadedGltf[];
  return {
    base: loaded[0],
    parts: parts.map((p, i) => ({ gltf: loaded[i + 1], part: p.part })),
    defaultHeightM: asset?.defaultHeightM ?? 1.75,
  };
}

/** Warm the cache for every base and part the roster wears, the moment the section opens. */
export function preloadFigureAssets(recipes: Array<FigureRecipe | null>): void {
  for (const recipe of recipes) {
    if (!recipe) continue;
    const urls = figureUrls(recipe);
    if (!urls) continue;
    useGLTF.preload(urls.base);
    for (const p of urls.parts) useGLTF.preload(p.url);
  }
}
