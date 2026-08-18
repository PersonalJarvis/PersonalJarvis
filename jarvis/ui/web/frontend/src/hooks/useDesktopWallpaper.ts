import { useEffect, useState } from "react";

import { useThemeValue } from "@/hooks/useTheme";
import { DEFAULT_WALLPAPER_URLS, defaultWallpaperUrl } from "@/lib/bundledWallpapers";
import { useWallpaperStore, wallpaperFullUrl } from "@/store/wallpaper";

// The bundled artwork itself — one picture per mode, each the default and the
// fallback of its own mode — is defined once in lib/bundledWallpapers, next to
// the other pictures that ship inside the app. Re-exported here because this
// hook is where the shell has always asked for it.
export { DEFAULT_WALLPAPER_URLS, defaultWallpaperUrl };

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
