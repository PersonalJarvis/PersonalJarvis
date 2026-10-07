/**
 * Best scores per retro game, kept in the browser's localStorage.
 *
 * Storage is a nicety, never a requirement: a private window, blocked site
 * data or a thumbnail renderer makes the storage accessor itself throw. Then
 * the best score lives in memory for the rest of the session instead.
 */

/** The localStorage key of a game's best score. */
export function bestKey(gameId: string): string {
  return `jarvis.office.arcade.${gameId}.best`;
}

export type ScoreStorage = Pick<Storage, "getItem" | "setItem">;

/** Fallback when the browser keeps nothing: the best scores of this session. */
const memory = new Map<string, number>();

function browserStorage(): ScoreStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    // Blocked site data throws on access; the in-memory fallback takes over.
    return null;
  }
}

function clean(score: number): number {
  return Number.isFinite(score) && score > 0 ? Math.floor(score) : 0;
}

/** The stored best score of a game, 0 when there is none. */
export function readBest(gameId: string, storage: ScoreStorage | null = browserStorage()): number {
  const key = bestKey(gameId);
  let stored = 0;
  if (storage) {
    try {
      stored = clean(Number(storage.getItem(key)));
    } catch {
      // Reading can throw like writing; fall back to the session's memory.
    }
  }
  return Math.max(stored, memory.get(key) ?? 0);
}

/**
 * Keep `score` if it beats the stored best. Returns true when it is a new
 * best (even if only memory could hold it).
 */
export function saveBest(gameId: string, score: number, storage: ScoreStorage | null = browserStorage()): boolean {
  const value = clean(score);
  if (value <= readBest(gameId, storage)) return false;
  const key = bestKey(gameId);
  memory.set(key, value);
  if (storage) {
    try {
      storage.setItem(key, String(value));
    } catch {
      // Quota or blocked storage: memory keeps it for this session.
    }
  }
  return true;
}

/** Test helper: forget the in-memory fallback. */
export function clearBestMemory(): void {
  memory.clear();
}
