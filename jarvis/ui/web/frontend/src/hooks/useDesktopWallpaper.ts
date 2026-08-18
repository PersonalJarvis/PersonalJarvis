import { useEffect, useState } from "react";

import jarvisDesktopWallpaperLight from "@/assets/jarvis-desktop-wallpaper-light.webp";
import jarvisDesktopWallpaper from "@/assets/jarvis-desktop-wallpaper.webp";
import { useThemeValue, type Theme } from "@/hooks/useTheme";
import { useWallpaperStore, wallpaperFullUrl } from "@/store/wallpaper";

/**
 * The artwork that ships inside the bundle — one picture per mode, each the
 * default and the fallback of its own mode.
 *
 * Two, not one, because a wallpaper belongs to exactly one mode (see
 * store/wallpaper.ts): the moonlit woodblock ocean is a night scene, and for
 * as long as it stood in for BOTH modes, light chrome landed on it whenever the
 * light slot was empty — a fresh profile, a cleared store, a manual switch to
 * light before any light picture was chosen — and nothing on that screen could
 * be read (maintainer report 2026-08-18). The daylight courtyard is the same
 * character in the light mode's own register, so a mode without a pick of its
 * own now shows a picture authored for it, never the other mode's.
 */
export const DEFAULT_WALLPAPER_URLS: Readonly<Record<Theme, string>> = {
  dark: jarvisDesktopWallpaper,
  light: jarvisDesktopWallpaperLight,
};

/** The bundled artwork of one mode. */
export function defaultWallpaperUrl(theme: Theme): string {
  return DEFAULT_WALLPAPER_URLS[theme];
}

/** The dark original — the picture the app was born with. */
export const DEFAULT_WALLPAPER_URL = DEFAULT_WALLPAPER_URLS.dark;

/**
 * The background-image URL the app shell should currently paint.
 *
 * A chosen wallpaper is a ~300 KB download from the library, and swapping the
 * CSS URL before those bytes arrive paints the shell empty for as long as the
 * transfer takes. So the new image is fetched into the browser cache first and
 * the switch happens only once it can be drawn — the visible change is then a
 * single repaint, with no gap in between.
 *
 * A failed load falls back to the bundled artwork of the mode instead of
 * leaving the shell bare. That is the ordinary case on a machine where the
 * wallpaper library was never generated: the id in localStorage is still
 * valid, the files simply are not there.
 */
export function useDesktopWallpaper(): string {
  // The pick is per theme: toggling the mode swaps in that mode's picture.
  const theme = useThemeValue();
  const selectedId = useWallpaperStore((state) => state.selections[theme]);
  const [url, setUrl] = useState(() => defaultWallpaperUrl(theme));

  useEffect(() => {
    const fallback = defaultWallpaperUrl(theme);
    if (!selectedId) {
      setUrl(fallback);
      return;
    }

    const target = wallpaperFullUrl(selectedId);
    let cancelled = false;
    const image = new Image();
    image.onload = () => {
      if (!cancelled) setUrl(target);
    };
    image.onerror = () => {
      if (!cancelled) setUrl(fallback);
    };
    image.src = target;
    // A cached image can be complete before the handlers are attached — in
    // that case no event will ever fire, so adopt it straight away.
    if (image.complete && image.naturalWidth > 0) setUrl(target);

    return () => {
      cancelled = true;
      image.onload = null;
      image.onerror = null;
    };
  }, [selectedId, theme]);

  return url;
}
