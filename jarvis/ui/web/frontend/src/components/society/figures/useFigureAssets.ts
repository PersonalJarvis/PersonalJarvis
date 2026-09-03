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
  /**
   * The clips a body borrows, when it ships none of its own. Every look built
   * on one rig shares a single copy — the base file would otherwise be five
   * parts animation to one part geometry — and three.js binds a clip by node
   * name, so the same clip drives every body of that rig.
   */
  clips: LoadedGltf | null;
}

export function figureUrls(
  recipe: FigureRecipe,
): { base: string; clips: string | null; parts: ReturnType<typeof partAssetsFor> } | null {
  const asset = figureAssetFor(recipe);
  if (!asset) return null;
  return { base: asset.url, clips: asset.clipsUrl, parts: partAssetsFor(recipe) };
}

/** Suspends. Call only when `figureAssetFor(recipe)` is non-null. */
export function useFigureAssets(recipe: FigureRecipe): LoadedFigureAssets {
  const asset = figureAssetFor(recipe);
  const parts = partAssetsFor(recipe);
  const clipsUrl = asset?.clipsUrl ?? null;
  const urls = [asset?.url ?? "", ...(clipsUrl ? [clipsUrl] : []), ...parts.map((p) => p.url)].filter(
    Boolean,
  );
  const loaded = useGLTF(urls) as unknown as LoadedGltf[];
  const partsAt = clipsUrl ? 2 : 1;
  return {
    base: loaded[0],
    clips: clipsUrl ? loaded[1] : null,
    parts: parts.map((p, i) => ({ gltf: loaded[i + partsAt], part: p.part })),
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
    if (urls.clips) useGLTF.preload(urls.clips);
    for (const p of urls.parts) useGLTF.preload(p.url);
  }
}
