import { create } from "zustand";

/**
 * Which desktop wallpaper the app is wearing.
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
const STORAGE_KEY = "jarvis.wallpaper.v1";

/** Where a chosen wallpaper's full-size file is served from. */
export function wallpaperFullUrl(id: string): string {
  return `/api/wallpapers/${encodeURIComponent(id)}/full`;
}

/** Where a chosen wallpaper's grid thumbnail is served from. */
export function wallpaperThumbUrl(id: string): string {
  return `/api/wallpapers/${encodeURIComponent(id)}/thumb`;
}

function readStoredId(): string | null {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored && stored.trim() ? stored : null;
  } catch {
    // Private mode, or storage disabled by policy — the default is a fine
    // answer, and a wallpaper is not worth failing a mount over.
    return null;
  }
}

interface WallpaperStore {
  /** The chosen wallpaper's id, or null for the bundled default. */
  selectedId: string | null;
  /** Persist a choice. Pass null to go back to the bundled default. */
  select: (id: string | null) => void;
  /** Adopt a choice made in another window. Does not write back. */
  adopt: (id: string | null) => void;
}

export const useWallpaperStore = create<WallpaperStore>((set) => ({
  selectedId: typeof window === "undefined" ? null : readStoredId(),
  select: (id) => {
    try {
      if (id) window.localStorage.setItem(STORAGE_KEY, id);
      else window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* the choice still applies to this window, it just will not survive */
    }
    set({ selectedId: id });
  },
  adopt: (id) => set({ selectedId: id }),
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
  const onStorage = (event: StorageEvent) => {
    if (event.key !== null && event.key !== STORAGE_KEY) return;
    useWallpaperStore.getState().adopt(readStoredId());
  };
  window.addEventListener("storage", onStorage);
  return () => window.removeEventListener("storage", onStorage);
}
