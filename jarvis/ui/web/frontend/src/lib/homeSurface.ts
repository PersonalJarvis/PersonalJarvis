/**
 * Which surface the front page ("chats" section) shows: the voice stage with
 * the Jarvis bar, or the typed chat.
 *
 * The two are one section with one switch at the top of the sidebar
 * (maintainer sketch, 2026-08-23) — `Voice | Chat` — not two sections: both
 * talk to the same assistant and share one history. Voice is the default;
 * it is what the product is for, and the typed chat is one click away.
 *
 * Persisted in localStorage so the choice survives a reload and a rebuild.
 * A broken or absent value falls back to voice: a corrupted preference must
 * land somewhere usable, never on a blank screen.
 */

export type HomeSurface = "voice" | "chat";

const STORAGE_KEY = "jarvis.home.surface.v1";
const DEFAULT_SURFACE: HomeSurface = "voice";

export function isHomeSurface(value: unknown): value is HomeSurface {
  return value === "voice" || value === "chat";
}

export function readHomeSurface(): HomeSurface {
  if (typeof window === "undefined") return DEFAULT_SURFACE;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return isHomeSurface(raw) ? raw : DEFAULT_SURFACE;
  } catch {
    return DEFAULT_SURFACE;
  }
}

export function writeHomeSurface(surface: HomeSurface): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, surface);
  } catch {
    /* not being able to remember the choice must not break switching it */
  }
}
