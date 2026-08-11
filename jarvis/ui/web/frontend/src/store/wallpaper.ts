import { create } from "zustand";

import { cachedTheme, type Theme } from "@/hooks/useTheme";

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

/** The single pre-per-theme slot. Migrated on load, never read live. */
const LEGACY_KEY = "jarvis.wallpaper.v1";

/**
 * One-time move of the pre-per-theme pick into a per-theme slot.
 *
 * The old slot used to be read as a live fallback for BOTH modes, and that is
 * how a night scene ended up behind light chrome: whichever mode had no pick
 * of its own inherited the other mode's picture, forever. A wallpaper belongs
 * to exactly one mode, so the fallback is gone and the stored pick is filed
 * once, under the mode it was authored for.
 *
 * The single-slot world switched the interface into every adopted picture's
 * mode and persisted that switch, so the cached theme at the moment this first
 * runs is the best witness for which mode the old pick belongs to. Worst case
 * it is filed under the wrong one and the next pick overwrites it — a one-time
 * cost, where the live fallback was a standing one.
 */
function migrateLegacySelection(): void {
  if (typeof window === "undefined") return;
  try {
    const legacy = window.localStorage.getItem(LEGACY_KEY);
    if (legacy && legacy.trim()) {
      const slot = THEME_KEYS[cachedTheme()];
      if (!window.localStorage.getItem(slot)) {
        window.localStorage.setItem(slot, legacy);
      }
    }
    window.localStorage.removeItem(LEGACY_KEY);
  } catch {
    /* storage blocked — then there is no stored pick to migrate either */
  }
}

/**
 * The shortlist of wallpapers worth coming back to.
 *
 * Deliberately NOT split per theme the way the selection is: a favourite is a
 * property of the picture, not of the mode the app happens to be in. Splitting
 * it would force someone who loves a daylight scene to re-star it after every
 * toggle, and the theme filter in the grid already answers "which of my
 * favourites fit dark mode".
 */
const FAVORITES_KEY = "jarvis.wallpaper.favorites.v1";

/**
 * An id minted by the upload store (`u` + 16 hex), as opposed to one written by
 * the library's manifest builder (`03-anime-neon-07`).
 *
 * The two stores are served from different paths, and the id is what tells them
 * apart — deliberately, so that everything holding only an id (the shell that
 * paints the background, a per-theme selection restored from storage) resolves
 * to the right picture without carrying a flag alongside it.
 */
const UPLOAD_ID_PATTERN = /^u[0-9a-f]{16}$/;

export function isUploadId(id: string): boolean {
  return UPLOAD_ID_PATTERN.test(id);
}

/** Where a chosen wallpaper's full-size file is served from. */
export function wallpaperFullUrl(id: string): string {
  const safe = encodeURIComponent(id);
  return isUploadId(id)
    ? `/api/wallpapers/uploads/${safe}/full`
    : `/api/wallpapers/${safe}/full`;
}

/** Where a chosen wallpaper's grid thumbnail is served from. */
export function wallpaperThumbUrl(id: string): string {
  const safe = encodeURIComponent(id);
  return isUploadId(id)
    ? `/api/wallpapers/uploads/${safe}/thumb`
    : `/api/wallpapers/${safe}/thumb`;
}

function readStoredId(theme: Theme): string | null {
  try {
    const own = window.localStorage.getItem(THEME_KEYS[theme]);
    return own && own.trim() ? own : null;
  } catch {
    // Private mode, or storage disabled by policy — the default is a fine
    // answer, and a wallpaper is not worth failing a mount over.
    return null;
  }
}

function readSelections(): Record<Theme, string | null> {
  if (typeof window === "undefined") return { light: null, dark: null };
  migrateLegacySelection();
  return { light: readStoredId("light"), dark: readStoredId("dark") };
}

/**
 * The stored favourites, defensively.
 *
 * Anything that is not a list of non-empty strings is treated as "no
 * favourites": the value is hand-editable browser storage, and a malformed
 * entry must cost the grid a shortlist, never a mount.
 */
function readFavorites(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(FAVORITES_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const clean = parsed.filter(
      (entry): entry is string => typeof entry === "string" && entry.trim() !== "",
    );
    return [...new Set(clean)];
  } catch {
    return [];
  }
}

interface WallpaperStore {
  /** Each theme's chosen wallpaper id, or null for the bundled default. */
  selections: Record<Theme, string | null>;
  /** Favourited wallpaper ids, oldest first. Shared by both themes. */
  favorites: string[];
  /** Persist a choice for one theme. Pass null to go back to the default. */
  select: (id: string | null, theme: Theme) => void;
  /**
   * Drop one wallpaper everywhere it is remembered — every theme slot and the
   * favourites. For a picture that no longer exists: an id kept for a deleted
   * upload would resolve to a 404 and silently fall back forever.
   */
  forget: (id: string) => void;
  /** Star or un-star one wallpaper. */
  toggleFavorite: (id: string) => void;
  /** Adopt choices made in another window. Does not write back. */
  adopt: () => void;
  /**
   * Re-file every stored pick under the mode its picture was authored for.
   *
   * The load-time migration above can only GUESS at the legacy pick's mode
   * (from the theme cache), and a wrong guess would park a night scene in the
   * light slot — the very leak all of this exists to end. This is the
   * correction pass, run by whoever holds the catalog and can therefore
   * answer for real: `themeOf` returns the mode a picture was authored for,
   * or null for an id it does not know (an upload still loading, a library
   * not installed) — unknown ids are left exactly where they are.
   */
  reconcile: (themeOf: (id: string) => Theme | null) => void;
}

export const useWallpaperStore = create<WallpaperStore>((set, get) => ({
  selections: readSelections(),
  favorites: readFavorites(),
  select: (id, theme) => {
    try {
      if (id) window.localStorage.setItem(THEME_KEYS[theme], id);
      else window.localStorage.removeItem(THEME_KEYS[theme]);
    } catch {
      /* the choice still applies to this window, it just will not survive */
    }
    set({ selections: readSelections() });
  },
  forget: (id) => {
    if (!id) return;
    try {
      for (const theme of ["light", "dark"] as Theme[]) {
        if (window.localStorage.getItem(THEME_KEYS[theme]) === id) {
          window.localStorage.removeItem(THEME_KEYS[theme]);
        }
      }
      if (window.localStorage.getItem(LEGACY_KEY) === id) {
        window.localStorage.removeItem(LEGACY_KEY);
      }
      const favorites = readFavorites().filter((entry) => entry !== id);
      if (favorites.length) {
        window.localStorage.setItem(FAVORITES_KEY, JSON.stringify(favorites));
      } else {
        window.localStorage.removeItem(FAVORITES_KEY);
      }
    } catch {
      /* nothing to clean up if storage is unavailable */
    }
    set({ selections: readSelections(), favorites: readFavorites() });
  },
  toggleFavorite: (id) => {
    if (!id) return;
    // Computed from the store rather than re-read from storage, so a rapid
    // double-click resolves against what this window last wrote.
    const current = get().favorites;
    const next = current.includes(id)
      ? current.filter((entry) => entry !== id)
      : [...current, id];
    try {
      if (next.length) window.localStorage.setItem(FAVORITES_KEY, JSON.stringify(next));
      else window.localStorage.removeItem(FAVORITES_KEY);
    } catch {
      /* the star still shows in this window, it just will not survive */
    }
    set({ favorites: next });
  },
  adopt: () => set({ selections: readSelections(), favorites: readFavorites() }),
  reconcile: (themeOf) => {
    try {
      for (const slot of ["light", "dark"] as Theme[]) {
        const id = window.localStorage.getItem(THEME_KEYS[slot]);
        if (!id || !id.trim()) continue;
        const authored = themeOf(id);
        if (authored === null || authored === slot) continue;
        // Misfiled: hand the pick to its own mode when that mode has not
        // chosen for itself, and clear the wrong slot either way — a mode
        // showing its default is right; one wearing the other mode's picture
        // is the bug.
        if (!window.localStorage.getItem(THEME_KEYS[authored])) {
          window.localStorage.setItem(THEME_KEYS[authored], id);
        }
        window.localStorage.removeItem(THEME_KEYS[slot]);
      }
    } catch {
      /* storage blocked — nothing stored means nothing misfiled */
    }
    set({ selections: readSelections() });
  },
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
  const watched = new Set<string>([
    LEGACY_KEY,
    THEME_KEYS.light,
    THEME_KEYS.dark,
    FAVORITES_KEY,
  ]);
  const onStorage = (event: StorageEvent) => {
    if (event.key !== null && !watched.has(event.key)) return;
    useWallpaperStore.getState().adopt();
  };
  window.addEventListener("storage", onStorage);
  return () => window.removeEventListener("storage", onStorage);
}
