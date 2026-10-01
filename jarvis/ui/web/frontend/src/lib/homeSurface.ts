/**
 * Which mode the front page ("chats" section) is in: the typed chat, or the
 * chat's voice mode (the live transcript with the Jarvis bar).
 *
 * Since 2026-10-01 the front page is ONE chat (maintainer: "no more Voice and
 * a normal chat — only a normal chat, with a button that turns voice mode
 * on"). Chat is the default; the voice mode is entered from the chat's top
 * bar or the assistant card and left the same way.
 *
 * Persisted in localStorage so a reload in the middle of a call stays in
 * voice mode. The key moved to v2 with the redesign so every install opens
 * on the chat once, whatever the old `Voice | Chat` switch last said. A
 * broken or absent value falls back to chat: a corrupted preference must
 * land somewhere usable, never on a blank screen.
 */

export type HomeSurface = "voice" | "chat";

const STORAGE_KEY = "jarvis.home.surface.v2";
const DEFAULT_SURFACE: HomeSurface = "chat";

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
