/**
 * Keep the section a window is showing in that window's own URL.
 *
 * The app reloads itself now: ./bundleWatch picks up a rebuilt frontend, and
 * ./preloadRecovery heals a window that crashed on a chunk a rebuild deleted.
 * Both replaced the Restart button, which is only an improvement if the user
 * comes back to the screen they were on — and until this module they did not.
 * The active section lives in the Zustand store, a reload discards it, and the
 * window came up on the default section every time: a workspace of terminals
 * still running behind a Chats screen, with the way back a trip through the nav.
 *
 * The URL is the one piece of window state a reload preserves, and this app
 * already READS `?view=` on boot — see `initialSectionFromSearch`, written so a
 * detached solo window knows which section it is. Writing the section back into
 * it adds no restore path at all; it feeds the one that exists.
 *
 * `replaceState`, never `pushState`: the desktop WebView has no back button,
 * and one history entry per section click would turn a trackpad back-swipe into
 * a walk through screens nobody navigated to.
 */

/** The query parameter `initialSectionFromSearch` reads on boot. */
export const SECTION_PARAM = "view";

/**
 * The same document address, now naming `section` — or null if it already does.
 *
 * Null means "nothing to write": a `replaceState` per store update would be a
 * needless history write on every unrelated re-render, and on some engines it is
 * rate-limited. Everything else in the address is carried over untouched, which
 * is load-bearing rather than tidy — `?solo=1` decides whether a window has
 * chrome, `?doc=` is which guide the Docs view reopens, and the hash is the
 * heading it scrolls to.
 */
export function hrefWithSection(href: string, section: string): string | null {
  const url = new URL(href);
  if (url.searchParams.get(SECTION_PARAM) === section) return null;
  url.searchParams.set(SECTION_PARAM, section);
  return `${url.pathname}${url.search}${url.hash}`;
}
