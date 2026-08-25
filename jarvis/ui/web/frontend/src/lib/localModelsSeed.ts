/**
 * Local, synchronous seed for the Local models section: the id of the one
 * provider card that can download models (`supports_model_pull`).
 *
 * The section used to wait for `/api/providers` before it mounted a single
 * panel — 2–3 s of nothing on a cold backend. The pull-capable id barely ever
 * changes, so the last resolved one is mirrored into `localStorage` (same
 * pattern as `assistantNameCache.ts`) and the view mounts its panels from the
 * seed at once while the provider list resolves in the background. When the
 * list arrives and the seeded card turns out NOT to be pull-capable any more,
 * the seed is cleared and the view falls back to the honest "no server" state.
 */

/** localStorage key holding the last pull-capable provider id. */
export const LOCAL_MODELS_SEED_KEY = "jarvis.localModels.pullProvider";

/**
 * Synchronously read the seeded provider id; `null` when none is stored or
 * storage is unavailable (private mode / embedded WebView policies).
 */
export function readLocalModelsSeed(): string | null {
  try {
    const raw = window.localStorage.getItem(LOCAL_MODELS_SEED_KEY);
    const trimmed = raw ? raw.trim() : "";
    return trimmed || null;
  } catch {
    return null;
  }
}

/** Persist the resolved id; empty ids are ignored, blocked storage is a no-op. */
export function writeLocalModelsSeed(providerId: string): void {
  try {
    const trimmed = (providerId || "").trim();
    if (trimmed) window.localStorage.setItem(LOCAL_MODELS_SEED_KEY, trimmed);
  } catch {
    // Storage disabled — non-fatal; the next open simply waits for the list.
  }
}

/** Forget the seed (the card is gone or lost its pull capability). */
export function clearLocalModelsSeed(): void {
  try {
    window.localStorage.removeItem(LOCAL_MODELS_SEED_KEY);
  } catch {
    // Nothing to recover: a seed that cannot be read cannot mislead either.
  }
}
