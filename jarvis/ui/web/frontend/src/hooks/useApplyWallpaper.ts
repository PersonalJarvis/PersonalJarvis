import { useCallback } from "react";

import { useTheme } from "@/hooks/useTheme";
import type { WallpaperEntry } from "@/hooks/useWallpaperCatalog";
import { useWallpaperStore } from "@/store/wallpaper";

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
 *
 * `null` means "back to the default": the current mode drops its pick and
 * shows the picture bundled for it, and the mode itself stays. It used to
 * clear the DARK slot and switch to dark regardless of where the user was —
 * so "Default" in light mode flipped the whole app and left the light pick in
 * place. Every mode has its own bundled picture now (useDesktopWallpaper), so
 * the default of the mode you are in is the only sensible reading.
 */
export function useApplyWallpaper(): (item: WallpaperEntry | null) => void {
  const select = useWallpaperStore((state) => state.select);
  const setBackground = useWallpaperStore((state) => state.setBackground);
  const { theme: current, setPreference } = useTheme();

  return useCallback(
    (item: WallpaperEntry | null) => {
      // Picking a picture is the act of wanting a wallpaper: the app paints
      // on a flat colour by default, and this is the one place that turns the
      // artwork on (lib/backgroundMode.ts). The "Solid colour" control in the
      // Wallpaper section is the way back.
      setBackground("wallpaper");
      if (item === null) {
        select(null, current);
        return;
      }
      // A bundled original has a grid tile of its own, but adopting it is the
      // same act as clearing the choice for its mode: one stored state for one
      // picture, so the tile and the "Default" button can never disagree.
      //
      // The pick is filed under the theme the picture was AUTHORED for — the
      // same theme the app is about to switch into. Each mode keeps its own
      // picture, so a later manual theme toggle brings the right one back.
      select(item.isDefault ? null : item.id, item.theme);
      setPreference(item.theme);
    },
    [select, setBackground, setPreference, current],
  );
}
