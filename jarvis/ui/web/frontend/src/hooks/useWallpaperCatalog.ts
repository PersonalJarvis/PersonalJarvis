import { useQuery } from "@tanstack/react-query";

import { defaultWallpaperUrl } from "@/hooks/useDesktopWallpaper";
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
   * True for the two wallpapers that ship inside the app rather than in the
   * generated library — one per mode. They are pinned to the front of the
   * grid and their pixels come from the bundle, not from `/api/wallpapers`.
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

/** The filter slug and chip label the bundled originals live under. */
export const DEFAULT_WALLPAPER_STYLE = "original";

/**
 * The wallpapers the app ships with — one per mode, each the picture its mode
 * falls back to.
 *
 * They are assembled here instead of being copied into the generated library
 * so that they are present unconditionally: the library is optional content
 * under a git-ignored directory, these pictures are part of the program. Their
 * pixels come from the same bundled assets the shell already paints, so
 * putting them in the grid costs no download at all.
 *
 * Two of them because a wallpaper belongs to one mode (see
 * useDesktopWallpaper.DEFAULT_WALLPAPER_URLS): the night scene the app was
 * born with, and its daylight twin for light mode. Each carries the mode it
 * was authored for, so adopting one switches the interface into that mode like
 * any other tile — and "back to the default" in light mode lands on the
 * daylight picture, never on the moonlit one.
 */
export const DEFAULT_WALLPAPER_ENTRIES: readonly WallpaperEntry[] = [
  {
    id: "original",
    title: "The Original",
    style: DEFAULT_WALLPAPER_STYLE,
    styleLabel: "Original",
    theme: "dark",
    isDefault: true,
  },
  {
    id: "original-light",
    title: "The Original, by day",
    style: DEFAULT_WALLPAPER_STYLE,
    styleLabel: "Original",
    theme: "light",
    isDefault: true,
  },
];

/** The dark original — the first wallpaper ever made for the app. */
export const DEFAULT_WALLPAPER_ENTRY: WallpaperEntry = DEFAULT_WALLPAPER_ENTRIES[0];

/** Where a grid tile's thumbnail comes from. */
export function thumbUrlFor(item: WallpaperEntry): string {
  return item.isDefault ? defaultWallpaperUrl(item.theme) : wallpaperThumbUrl(item.id);
}

/** Where a preview's full-size image comes from. */
export function fullUrlFor(item: WallpaperEntry): string {
  return item.isDefault ? defaultWallpaperUrl(item.theme) : wallpaperFullUrl(item.id);
}

/** The catalog as it looks before (or without) the generated library. */
const BUNDLED_ONLY: WallpaperCatalog = {
  available: true,
  libraryAvailable: false,
  count: DEFAULT_WALLPAPER_ENTRIES.length,
  styles: [
    { slug: DEFAULT_WALLPAPER_STYLE, label: "Original", count: DEFAULT_WALLPAPER_ENTRIES.length },
  ],
  items: [...DEFAULT_WALLPAPER_ENTRIES],
};

/**
 * Fetch the wallpaper catalog.
 *
 * The payload is a few dozen kilobytes of metadata for five hundred entries
 * and it only changes when the library on disk is regenerated, so it is
 * fetched once per session and kept — refetching it on every visit to the
 * section would buy nothing.
 *
 * The bundled originals are prepended to whatever the server returns, so the
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
      // original is still a wallpaper, and showing it beats showing a stack
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
          DEFAULT_WALLPAPER_ENTRIES.length,
        styles: [BUNDLED_ONLY.styles[0], ...styles],
        items: [...DEFAULT_WALLPAPER_ENTRIES, ...data.items],
      };
    },
  });
}
