import { useQuery } from "@tanstack/react-query";

/** One wallpaper as the browse grid knows it — metadata only, no pixels. */
export interface WallpaperEntry {
  id: string;
  title: string;
  /** Directory slug, e.g. `03-anime-neon`. Stable; used as the filter key. */
  style: string;
  /** Written-out style name for the filter chips, e.g. `Cinematic Anime Neon`. */
  styleLabel: string;
  theme: "light" | "dark";
}

export interface WallpaperStyle {
  slug: string;
  label: string;
  count: number;
}

export interface WallpaperCatalog {
  /** False when the generated library is not installed on this machine. */
  available: boolean;
  count: number;
  styles: WallpaperStyle[];
  items: WallpaperEntry[];
}

const EMPTY_CATALOG: WallpaperCatalog = {
  available: false,
  count: 0,
  styles: [],
  items: [],
};

/**
 * Fetch the wallpaper catalog.
 *
 * The payload is a few dozen kilobytes of metadata for five hundred entries
 * and it only changes when the library on disk is regenerated, so it is
 * fetched once per session and kept — refetching it on every visit to the
 * section would buy nothing.
 */
export function useWallpaperCatalog() {
  return useQuery<WallpaperCatalog>({
    queryKey: ["wallpapers"],
    staleTime: Infinity,
    retry: 1,
    queryFn: async () => {
      const response = await fetch("/api/wallpapers");
      if (!response.ok) throw new Error(`Wallpaper catalog failed: ${response.status}`);
      const data = (await response.json()) as Partial<WallpaperCatalog> | null;
      if (!data || !Array.isArray(data.items)) return EMPTY_CATALOG;
      return {
        available: Boolean(data.available),
        count: typeof data.count === "number" ? data.count : data.items.length,
        styles: Array.isArray(data.styles) ? data.styles : [],
        items: data.items,
      };
    },
  });
}
