import { useCallback } from "react";

import { useTheme, type Theme } from "@/hooks/useTheme";
import type { WallpaperEntry } from "@/hooks/useWallpaperCatalog";
import { useWallpaperStore } from "@/store/wallpaper";

/**
 * The theme the bundled artwork belongs to.
 *
 * It is a night scene — a moonlit woodblock ocean — so returning to the default
 * wallpaper returns to dark mode. This also happens to be the app's own default
 * preference, which is why a fresh profile is consistent from the first paint.
 */
export const DEFAULT_WALLPAPER_THEME: Theme = "dark";

/**
 * Adopt a wallpaper, and take the interface theme with it.
 *
 * Light and dark mode here are not an independent setting the user has to keep
 * in sync by hand: the wallpaper IS the app's ground, and every one of the five
 * hundred is authored for one of the two. A bright daylight scene under dark
 * chrome reads as a mistake, so choosing the picture chooses the mode.
 *
 * Both halves persist through the machinery that already owns them — the
 * wallpaper through localStorage, the theme through the appearance endpoint —
 * so nothing here invents a second source of truth. The theme switcher in
 * Settings keeps working afterwards; it is the next wallpaper, not this one,
 * that overrides a manual change.
 */
export function useApplyWallpaper(): (item: WallpaperEntry | null) => void {
  const select = useWallpaperStore((state) => state.select);
  const { setPreference } = useTheme();

  return useCallback(
    (item: WallpaperEntry | null) => {
      // The bundled original has a grid tile of its own, but adopting it is
      // the same act as clearing the choice: one stored state for one picture,
      // so the tile and the "Default" button can never disagree.
      //
      // The pick is filed under the theme the picture was AUTHORED for — the
      // same theme the app is about to switch into. Each mode keeps its own
      // picture, so a later manual theme toggle brings the right one back.
      const theme = item?.theme ?? DEFAULT_WALLPAPER_THEME;
      select(item && !item.isDefault ? item.id : null, theme);
      setPreference(theme);
    },
    [select, setPreference],
  );
}
