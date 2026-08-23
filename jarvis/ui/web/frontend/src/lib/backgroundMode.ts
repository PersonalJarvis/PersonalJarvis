/**
 * What the app is painted ON: a flat theme colour, or a wallpaper.
 *
 * The flat colour is the default (maintainer, 2026-08-23): the app no longer
 * opens on artwork. A wallpaper stays one click away in the Wallpaper
 * section — choosing a picture there switches this to `wallpaper`, and the
 * "Solid colour" control switches it back. Every wallpaper pick the user has
 * made is kept either way; this only says whether it is shown.
 *
 * Two readers must agree on the answer before the first paint: the inline
 * boot script in index.html (which stamps the `jarvis-wallpaper` class on
 * <html> so the wallpaper readability floor in index.css applies from the
 * first frame) and the wallpaper store once React owns the document. Both
 * read THIS key with THIS fallback — keep them in lockstep.
 */

export type BackgroundMode = "solid" | "wallpaper";

export const BACKGROUND_MODE_KEY = "jarvis.background.v1";
export const DEFAULT_BACKGROUND_MODE: BackgroundMode = "solid";

/** The class index.css keys the wallpaper readability floor on. */
export const WALLPAPER_CLASS = "jarvis-wallpaper";

export function isBackgroundMode(value: unknown): value is BackgroundMode {
  return value === "solid" || value === "wallpaper";
}

export function readBackgroundMode(): BackgroundMode {
  if (typeof window === "undefined") return DEFAULT_BACKGROUND_MODE;
  try {
    const raw = window.localStorage.getItem(BACKGROUND_MODE_KEY);
    return isBackgroundMode(raw) ? raw : DEFAULT_BACKGROUND_MODE;
  } catch {
    // Private mode / storage disabled — the flat colour still has to paint.
    return DEFAULT_BACKGROUND_MODE;
  }
}

export function writeBackgroundMode(mode: BackgroundMode): void {
  try {
    window.localStorage.setItem(BACKGROUND_MODE_KEY, mode);
  } catch {
    /* not being able to remember the choice must not break applying it */
  }
}

/**
 * Put the document into the given mode — the class is what index.css reads.
 * Idempotent, safe to call on every store change.
 */
export function applyBackgroundClass(mode: BackgroundMode): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle(WALLPAPER_CLASS, mode === "wallpaper");
}
