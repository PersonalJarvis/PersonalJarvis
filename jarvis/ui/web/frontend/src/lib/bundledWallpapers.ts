import jarvisDesktopWallpaperLight from "@/assets/jarvis-desktop-wallpaper-light.webp";
import jarvisDesktopWallpaper from "@/assets/jarvis-desktop-wallpaper.webp";
import type { Theme } from "@/hooks/useTheme";

/**
 * The wallpapers that ship INSIDE the app — the one place that knows them.
 *
 * Everything else in the wallpaper section comes from somewhere optional: the
 * generated library is a git-ignored download, the uploads live in the data
 * directory, the marketplace needs a network. These few are part of the
 * program, so a fresh checkout, a headless container, and a machine that never
 * downloaded anything all have them — and every consumer that holds only an id
 * (the shell painting the ground, a pick restored from storage, a grid tile)
 * resolves it here first, before asking `/api/wallpapers`.
 *
 * Two kinds live in this list:
 *
 * - The two ORIGINALS, one per mode. Each is the default and the fallback of
 *   its mode: an empty selection means "this one" (see store/wallpaper.ts and
 *   useDesktopWallpaper), and adopting it is the same act as clearing the
 *   choice.
 * - The PLAIN grounds — pure black for dark, pure white for light. Ordinary
 *   wallpapers in every respect (stored by id, starred, filtered), just with
 *   no pixels to fetch: a solid colour is drawn, not downloaded, so each is a
 *   tiny SVG data URI instead of an asset file.
 *
 * Every entry is authored for exactly one mode, like every other wallpaper —
 * adopting one switches the interface into that mode.
 */
export interface BundledWallpaper {
  id: string;
  title: string;
  /** Filter slug shared with the catalog's style chips. */
  style: string;
  styleLabel: string;
  theme: Theme;
  /** Where the pixels come from — an asset URL or a data URI, never `/api/`. */
  url: string;
  /** True for the one wallpaper a mode falls back to. Exactly one per mode. */
  isDefault: boolean;
}

/** The filter slug and chip label the two originals live under. */
export const ORIGINAL_WALLPAPER_STYLE = "original";

/** The filter slug and chip label the plain black/white grounds live under. */
export const PLAIN_WALLPAPER_STYLE = "plain";

/**
 * A full-frame solid colour as an image URL.
 *
 * An SVG data URI rather than a PNG in `assets/`: it IS the colour, byte for
 * byte readable, and it scales without a thumbnail. Kept free of parentheses
 * and quotes on purpose — the shell puts it into an unquoted CSS `url(...)`.
 */
export function solidColorWallpaperUrl(hex: string): string {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080">` +
    `<rect width="100%" height="100%" fill="${hex}"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

/** In the order they appear in the grid: night, day, black, white. */
export const BUNDLED_WALLPAPERS: readonly BundledWallpaper[] = [
  {
    id: "original",
    title: "The Original",
    style: ORIGINAL_WALLPAPER_STYLE,
    styleLabel: "Original",
    theme: "dark",
    url: jarvisDesktopWallpaper,
    isDefault: true,
  },
  {
    id: "original-light",
    title: "The Original, by day",
    style: ORIGINAL_WALLPAPER_STYLE,
    styleLabel: "Original",
    theme: "light",
    url: jarvisDesktopWallpaperLight,
    isDefault: true,
  },
  {
    id: "plain-black",
    title: "Black",
    style: PLAIN_WALLPAPER_STYLE,
    styleLabel: "Plain",
    theme: "dark",
    url: solidColorWallpaperUrl("#000000"),
    isDefault: false,
  },
  {
    id: "plain-white",
    title: "White",
    style: PLAIN_WALLPAPER_STYLE,
    styleLabel: "Plain",
    theme: "light",
    url: solidColorWallpaperUrl("#ffffff"),
    isDefault: false,
  },
];

const BY_ID: ReadonlyMap<string, BundledWallpaper> = new Map(
  BUNDLED_WALLPAPERS.map((entry) => [entry.id, entry]),
);

/** True for an id whose pixels ship with the app. */
export function isBundledId(id: string): boolean {
  return BY_ID.has(id);
}

/** The bundled picture behind an id, or null for one served from the API. */
export function bundledWallpaperUrl(id: string): string | null {
  return BY_ID.get(id)?.url ?? null;
}

/**
 * The picture each mode falls back to — the original authored for it.
 *
 * Two, not one, because a wallpaper belongs to exactly one mode: the moonlit
 * woodblock ocean is a night scene, and for as long as it stood in for BOTH
 * modes, light chrome landed on it whenever the light slot was empty — a
 * fresh profile, a cleared store, a manual switch to light before any light
 * picture was chosen — and nothing on that screen could be read (maintainer
 * report 2026-08-18). The daylight courtyard is the same character in the
 * light mode's own register, so a mode without a pick of its own shows a
 * picture authored for it, never the other mode's.
 */
export const DEFAULT_WALLPAPER_URLS: Readonly<Record<Theme, string>> = {
  dark: defaultOf("dark"),
  light: defaultOf("light"),
};

function defaultOf(theme: Theme): string {
  const entry = BUNDLED_WALLPAPERS.find((item) => item.isDefault && item.theme === theme);
  if (!entry) throw new Error(`No bundled default wallpaper for ${theme} mode`);
  return entry.url;
}

/** The bundled artwork of one mode. */
export function defaultWallpaperUrl(theme: Theme): string {
  return DEFAULT_WALLPAPER_URLS[theme];
}
