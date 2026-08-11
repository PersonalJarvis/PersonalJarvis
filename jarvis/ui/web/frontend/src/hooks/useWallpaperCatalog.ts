import { useQuery } from "@tanstack/react-query";

import { DEFAULT_WALLPAPER_URL } from "@/hooks/useDesktopWallpaper";
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
   * True for the one wallpaper that ships inside the app rather than in the
   * generated library. It is pinned to the front of the grid and its pixels
   * come from the bundle, not from `/api/wallpapers`.
   */
  isDefault?: boolean;
  /**
   * True for a picture the owner uploaded. Those are the only wallpapers that
   * can be removed or re-themed, so the preview needs to know which it has.
   */
  isUpload?: boolean;
}

export interface WallpaperStyle {
  slug: string;
  label: string;
  count: number;
}

export interface WallpaperCatalog {
  /** Always true — the bundled original is a wallpaper the grid can show. */
  available: boolean;
  /** False when the generated 500-piece library is not installed here. */
  libraryAvailable: boolean;
  count: number;
  styles: WallpaperStyle[];
  items: WallpaperEntry[];
}

/** The filter slug and chip label the bundled original lives under. */
export const DEFAULT_WALLPAPER_STYLE = "original";

/**
 * The wallpaper the app ships with — the first one ever made for it, and the
 * one it falls back to.
 *
 * It is assembled here instead of being copied into the generated library so
 * that it is present unconditionally: the library is optional content under a
 * git-ignored directory, this picture is part of the program. Its pixels come
 * from the same bundled asset the shell already paints, so putting it in the
 * grid costs no download at all.
 */
export const DEFAULT_WALLPAPER_ENTRY: WallpaperEntry = {
  id: "original",
  title: "The Original",
  style: DEFAULT_WALLPAPER_STYLE,
  styleLabel: "Original",
  // A moonlit woodblock ocean — see useApplyWallpaper.DEFAULT_WALLPAPER_THEME,
  // which has to agree with this or returning to the default would flip the
  // interface to the wrong mode.
  theme: "dark",
  isDefault: true,
};

/** Where a grid tile's thumbnail comes from. */
export function thumbUrlFor(item: WallpaperEntry): string {
  return item.isDefault ? DEFAULT_WALLPAPER_URL : wallpaperThumbUrl(item.id);
}

/** Where a preview's full-size image comes from. */
export function fullUrlFor(item: WallpaperEntry): string {
  return item.isDefault ? DEFAULT_WALLPAPER_URL : wallpaperFullUrl(item.id);
}

/** The catalog as it looks before (or without) the generated library. */
const BUNDLED_ONLY: WallpaperCatalog = {
  available: true,
  libraryAvailable: false,
  count: 1,
  styles: [
    { slug: DEFAULT_WALLPAPER_STYLE, label: "Original", count: 1 },
  ],
  items: [DEFAULT_WALLPAPER_ENTRY],
};

/**
 * Fetch the wallpaper catalog.
 *
 * The payload is a few dozen kilobytes of metadata for five hundred entries
 * and it only changes when the library on disk is regenerated, so it is
 * fetched once per session and kept — refetching it on every visit to the
 * section would buy nothing.
 *
 * The bundled original is prepended to whatever the server returns, so the
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
        count: (typeof data.count === "number" ? data.count : data.items.length) + 1,
        styles: [BUNDLED_ONLY.styles[0], ...styles],
        items: [DEFAULT_WALLPAPER_ENTRY, ...data.items],
      };
    },
  });
}
