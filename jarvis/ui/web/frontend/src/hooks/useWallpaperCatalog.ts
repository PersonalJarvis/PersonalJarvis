import { useQuery } from "@tanstack/react-query";

import {
  BUNDLED_WALLPAPERS,
  ORIGINAL_WALLPAPER_STYLE,
  PLAIN_WALLPAPER_STYLE,
} from "@/lib/bundledWallpapers";
import { wallpaperFullUrl, wallpaperThumbUrl } from "@/store/wallpaper";

/** One wallpaper as the browse grid knows it — metadata only, no pixels. */
export interface WallpaperEntry {
  id: string;
  title: string;
  /** Directory slug, e.g. `03-anime-neon`. Stable; used as the filter key. */
  style: string;
  /** Written-out style name for the filter chips, e.g. `Cinematic Anime Neon`. */
  styleLabel: string;
  theme: "light" | "dark";
  /**
   * True for a wallpaper that ships inside the app rather than in the
   * generated library: the two originals and the plain black/white grounds.
   * They are pinned to the front of the grid and their pixels come from the
   * bundle, not from `/api/wallpapers` (see lib/bundledWallpapers).
   */
  isBundled?: boolean;
  /**
   * True for the one bundled wallpaper each mode falls back to — the original
   * of that mode. Adopting it clears the stored choice instead of storing an
   * id, so the tile and the "Default" button can never disagree.
   */
  isDefault?: boolean;
  /**
   * True for a picture the owner uploaded. Those are the only wallpapers that
   * can be removed or re-themed, so the preview needs to know which it has.
   * An installed marketplace wallpaper lives in the same store and is an
   * upload in that sense: it can be re-themed and removed the same way.
   */
  isUpload?: boolean;
  /** True for a picture installed from the community marketplace. */
  fromMarketplace?: boolean;
  /** GitHub login that published it, when the entry carried one. */
  publisher?: string | null;
}

export interface WallpaperStyle {
  slug: string;
  label: string;
  count: number;
}

export interface WallpaperCatalog {
  /** Always true — the bundled originals are wallpapers the grid can show. */
  available: boolean;
  /** False when the generated 500-piece library is not installed here. */
  libraryAvailable: boolean;
  count: number;
  styles: WallpaperStyle[];
  items: WallpaperEntry[];
}

/**
 * The wallpapers the app ships with, as the grid knows them.
 *
 * They are assembled here instead of being copied into the generated library
 * so that they are present unconditionally: the library is optional content
 * under a git-ignored directory, these pictures are part of the program. Their
 * pixels come from the bundle (the same assets the shell already paints, or a
 * drawn solid colour), so putting them in the grid costs no download at all.
 *
 * Which pictures, and why, is the business of lib/bundledWallpapers — the two
 * originals (one per mode, each its mode's default) and the plain black and
 * white grounds. Every one carries the mode it was authored for, so adopting
 * it switches the interface into that mode like any other tile.
 */
export const BUNDLED_WALLPAPER_ENTRIES: readonly WallpaperEntry[] = BUNDLED_WALLPAPERS.map(
  ({ id, title, style, styleLabel, theme, isDefault }) => ({
    id,
    title,
    style,
    styleLabel,
    theme,
    isBundled: true,
    ...(isDefault ? { isDefault: true } : {}),
  }),
);

/**
 * The style chips for the bundled wallpapers, in the order they lead the chip
 * row: the originals, then the plain grounds. Counts come from the entries so
 * a chip can never promise a tile the grid does not have.
 */
export const BUNDLED_WALLPAPER_STYLES: readonly WallpaperStyle[] = [
  { slug: ORIGINAL_WALLPAPER_STYLE, label: "Original" },
  { slug: PLAIN_WALLPAPER_STYLE, label: "Plain" },
].map(({ slug, label }) => ({
  slug,
  label,
  count: BUNDLED_WALLPAPER_ENTRIES.filter((entry) => entry.style === slug).length,
}));

/** True for a chip that stands for bundled wallpapers rather than library ones. */
export function isBundledStyle(slug: string): boolean {
  return BUNDLED_WALLPAPER_STYLES.some((chip) => chip.slug === slug);
}

/**
 * Where a grid tile's thumbnail comes from.
 *
 * The URL helpers already answer with the bundled picture for a bundled id, so
 * a tile needs no special case here — the id is the whole story.
 */
export function thumbUrlFor(item: WallpaperEntry): string {
  return wallpaperThumbUrl(item.id);
}

/** Where a preview's full-size image comes from. */
export function fullUrlFor(item: WallpaperEntry): string {
  return wallpaperFullUrl(item.id);
}

/** The catalog as it looks before (or without) the generated library. */
const BUNDLED_ONLY: WallpaperCatalog = {
  available: true,
  libraryAvailable: false,
  count: BUNDLED_WALLPAPER_ENTRIES.length,
  styles: [...BUNDLED_WALLPAPER_STYLES],
  items: [...BUNDLED_WALLPAPER_ENTRIES],
};

/**
 * Fetch the wallpaper catalog.
 *
 * The payload is a few dozen kilobytes of metadata for five hundred entries
 * and it only changes when the library on disk is regenerated, so it is
 * fetched once per session and kept — refetching it on every visit to the
 * section would buy nothing.
 *
 * The bundled wallpapers are prepended to whatever the server returns, so the
 * section has something to show even when the request fails or the library was
 * never generated on this machine.
 */
export function useWallpaperCatalog() {
  return useQuery<WallpaperCatalog>({
    queryKey: ["wallpapers"],
    staleTime: Infinity,
    retry: 1,
    queryFn: async () => {
      // A failure here is not an error state for the section: the bundled
      // pictures are still wallpapers, and showing them beats showing a stack
      // trace because a backend was mid-restart.
      const data = await fetch("/api/wallpapers")
        .then((response) =>
          response.ok
            ? (response.json() as Promise<Partial<WallpaperCatalog> | null>)
            : null,
        )
        .catch(() => null);
      if (!data || !Array.isArray(data.items) || !data.items.length) {
        return BUNDLED_ONLY;
      }
      const styles = Array.isArray(data.styles) ? data.styles : [];
      return {
        available: true,
        libraryAvailable: Boolean(data.available),
        count:
          (typeof data.count === "number" ? data.count : data.items.length) +
          BUNDLED_WALLPAPER_ENTRIES.length,
        styles: [...BUNDLED_WALLPAPER_STYLES, ...styles],
        items: [...BUNDLED_WALLPAPER_ENTRIES, ...data.items],
      };
    },
  });
}
