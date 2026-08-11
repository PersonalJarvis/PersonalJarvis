import { useEffect, useState } from "react";

import jarvisDesktopWallpaper from "@/assets/jarvis-desktop-wallpaper.webp";
import { useThemeValue } from "@/hooks/useTheme";
import { useWallpaperStore, wallpaperFullUrl } from "@/store/wallpaper";

/** The artwork that ships inside the bundle — the default and the fallback. */
export const DEFAULT_WALLPAPER_URL = jarvisDesktopWallpaper;

/**
 * The background-image URL the app shell should currently paint.
 *
 * A chosen wallpaper is a ~300 KB download from the library, and swapping the
 * CSS URL before those bytes arrive paints the shell empty for as long as the
 * transfer takes. So the new image is fetched into the browser cache first and
 * the switch happens only once it can be drawn — the visible change is then a
 * single repaint, with no gap in between.
 *
 * A failed load falls back to the bundled artwork instead of leaving the shell
 * bare. That is the ordinary case on a machine where the wallpaper library was
 * never generated: the id in localStorage is still valid, the files simply are
 * not there.
 */
export function useDesktopWallpaper(): string {
  // The pick is per theme: toggling the mode swaps in that mode's picture.
  const theme = useThemeValue();
  const selectedId = useWallpaperStore((state) => state.selections[theme]);
  const [url, setUrl] = useState(DEFAULT_WALLPAPER_URL);

  useEffect(() => {
    if (!selectedId) {
      setUrl(DEFAULT_WALLPAPER_URL);
      return;
    }

    const target = wallpaperFullUrl(selectedId);
    let cancelled = false;
    const image = new Image();
    image.onload = () => {
      if (!cancelled) setUrl(target);
    };
    image.onerror = () => {
      if (!cancelled) setUrl(DEFAULT_WALLPAPER_URL);
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
  }, [selectedId]);

  return url;
}
