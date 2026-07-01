/**
 * Local, synchronous cache for the resolved assistant display name.
 *
 * The assistant's name is derived from the user's configured wake word (e.g.
 * "Nico" / "Athena"). It lives authoritatively on the backend and is fetched by
 * `useAssistantNameSeed` after mount. That fetch is asynchronous, so during the
 * first paint the store would otherwise fall back to a hardcoded placeholder —
 * which MUST NEVER be a trademarked name such as "Jarvis" (Marvel), because the
 * placeholder is briefly visible before the real name resolves. A single frame
 * of a trademarked character name is a real legal exposure for redistributors,
 * so the placeholder is always trademark-free.
 *
 * To make the user's own name appear instantly at boot with ZERO added latency,
 * we mirror the last resolved name into `localStorage` — a synchronous,
 * in-process read with no network round-trip. The store seeds from this cache,
 * and `index.html` paints the boot splash from it via a tiny inline script. On
 * the very first run the cache is empty and the neutral fallback is used until
 * the fetch resolves.
 *
 * The storage key is duplicated verbatim in `index.html`'s inline boot script —
 * keep the two in lockstep.
 */

/** localStorage key holding the last resolved assistant name. */
export const ASSISTANT_NAME_CACHE_KEY = "jarvis.assistantName";

/**
 * Neutral, trademark-free fallback used only until a real name is known (first
 * run, or a blocked/empty cache). Matches the backend's neutral default in
 * `jarvis.brain.assistant_name.resolve_assistant_name`. It is intentionally a
 * generic word, never a product or character name — and never empty, because
 * `interpolateName` leaves the `{name}` token intact for an empty value, which
 * would surface a literal "{name}" in the UI.
 */
export const NEUTRAL_ASSISTANT_NAME = "Assistant";

/**
 * Synchronously read the cached assistant name. Returns `fallback` when no name
 * has been cached yet or storage is unavailable (private mode / headless).
 */
export function readCachedAssistantName(
  fallback: string = NEUTRAL_ASSISTANT_NAME,
): string {
  try {
    const cached = window.localStorage.getItem(ASSISTANT_NAME_CACHE_KEY);
    const trimmed = cached ? cached.trim() : "";
    return trimmed || fallback;
  } catch {
    return fallback;
  }
}

/**
 * Persist the resolved name so the next boot can paint it instantly. Empty or
 * whitespace-only names are ignored (they would blank the wordmark). A blocked
 * storage (private mode) is a silent no-op — the async fetch still seeds the
 * store on that run.
 */
export function writeCachedAssistantName(name: string): void {
  try {
    const trimmed = (name || "").trim();
    if (trimmed) window.localStorage.setItem(ASSISTANT_NAME_CACHE_KEY, trimmed);
  } catch {
    // Storage disabled — non-fatal; the fetch path still seeds the store.
  }
}
