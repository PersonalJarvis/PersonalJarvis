import { create } from "zustand";

import type { Theme } from "@/hooks/useTheme";

/**
 * Which desktop wallpaper the app is wearing — one choice PER THEME.
 *
 * Every catalog picture is authored for one of the two modes (a daylight
 * terrace for light, a moonlit ocean for dark), and picking one switches the
 * app into that mode (`useApplyWallpaper`). A single shared slot therefore
 * could not survive a manual theme toggle: the bright picture stayed behind
 * dark chrome and read as a mistake. Each mode keeps its own pick instead,
 * and toggling the theme brings that mode's picture back with it.
 *
 * The bundled artwork stays the default and the fallback: an empty selection
 * means "the one that ships with the app", so a fresh profile, a cleared
 * browser storage, and a checkout without the generated library all land on
 * the same known-good picture rather than on a blank shell.
 *
 * The choice lives in localStorage rather than on the server. It is a per-
 * screen cosmetic preference with no backend meaning, and localStorage is the
 * one store every window of the app — including the detached solo windows —
 * already shares.
 */
const THEME_KEYS: Record<Theme, string> = {
  light: "jarvis.wallpaper.light.v1",
  dark: "jarvis.wallpaper.dark.v1",
};

/** The pre-per-theme slot. Kept readable as the fallback for a mode that has
 *  no pick of its own yet, so an existing profile keeps its picture. */
const LEGACY_KEY = "jarvis.wallpaper.v1";

/** Where a chosen wallpaper's full-size file is served from. */
export function wallpaperFullUrl(id: string): string {
  return `/api/wallpapers/${encodeURIComponent(id)}/full`;
}

/** Where a chosen wallpaper's grid thumbnail is served from. */
export function wallpaperThumbUrl(id: string): string {
  return `/api/wallpapers/${encodeURIComponent(id)}/thumb`;
}

function readStoredId(theme: Theme): string | null {
  try {
    const own = window.localStorage.getItem(THEME_KEYS[theme]);
    if (own && own.trim()) return own;
    const legacy = window.localStorage.getItem(LEGACY_KEY);
    return legacy && legacy.trim() ? legacy : null;
  } catch {
    // Private mode, or storage disabled by policy — the default is a fine
    // answer, and a wallpaper is not worth failing a mount over.
    return null;
  }
}

function readSelections(): Record<Theme, string | null> {
  if (typeof window === "undefined") return { light: null, dark: null };
  return { light: readStoredId("light"), dark: readStoredId("dark") };
}

interface WallpaperStore {
  /** Each theme's chosen wallpaper id, or null for the bundled default. */
  selections: Record<Theme, string | null>;
  /** Persist a choice for one theme. Pass null to go back to the default. */
  select: (id: string | null, theme: Theme) => void;
  /** Adopt choices made in another window. Does not write back. */
  adopt: () => void;
}

export const useWallpaperStore = create<WallpaperStore>((set) => ({
  selections: readSelections(),
  select: (id, theme) => {
    try {
      if (id) window.localStorage.setItem(THEME_KEYS[theme], id);
      else window.localStorage.removeItem(THEME_KEYS[theme]);
      // The legacy slot must not outvote an explicit "back to default": it is
      // only a fallback for modes that never chose, so an empty per-theme pick
      // needs the legacy value gone or it would resurrect the old picture.
      if (!id) window.localStorage.removeItem(LEGACY_KEY);
    } catch {
      /* the choice still applies to this window, it just will not survive */
    }
    set({ selections: readSelections() });
  },
  adopt: () => set({ selections: readSelections() }),
}));

/**
 * Keep every window on the same wallpaper.
 *
 * A `storage` event fires in the *other* documents of the origin, which is
 * exactly the set of windows that need to repaint: pick a wallpaper in the
 * main window and the detached ones follow without a reload.
 */
export function installWallpaperSync(): () => void {
  if (typeof window === "undefined") return () => {};
  const watched = new Set<string>([LEGACY_KEY, THEME_KEYS.light, THEME_KEYS.dark]);
  const onStorage = (event: StorageEvent) => {
    if (event.key !== null && !watched.has(event.key)) return;
    useWallpaperStore.getState().adopt();
  };
  window.addEventListener("storage", onStorage);
  return () => window.removeEventListener("storage", onStorage);
}
