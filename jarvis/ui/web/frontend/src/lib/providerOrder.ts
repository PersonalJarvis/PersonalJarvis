import { useCallback, useSyncExternalStore } from "react";

/**
 * The order a person dragged the providers into on a model picker's rail.
 *
 * One order for every picker (front page, threads, agent cards), kept in
 * this browser only — a convenience, not a setting, so cleared storage just
 * restores the catalog's own order. Providers never placed keep their
 * catalog position relative to each other, after the placed ones.
 */

const KEY = "jarvis.chat.providerOrder";
const listeners = new Set<() => void>();
// useSyncExternalStore needs one stable array per stored value.
let cached: { raw: string | null; ids: string[] } = { raw: null, ids: [] };

function read(): string[] {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(KEY);
  } catch {
    // Blocked storage: fall back to the catalog order.
  }
  if (raw === cached.raw) return cached.ids;
  let ids: string[] = [];
  try {
    const parsed: unknown = JSON.parse(raw ?? "[]");
    ids = Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : [];
  } catch {
    // Corrupt value: fall back to the catalog order.
  }
  cached = { raw, ids };
  return ids;
}

function write(next: string[]): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Storage refused the write: keep the order for this visit only.
    cached = { raw: cached.raw, ids: next };
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Sorts `items` by the saved order; unplaced items follow in their own order. */
export function orderBy<T>(items: T[], idOf: (item: T) => string, saved: string[]): T[] {
  const rank = new Map(saved.map((id, index) => [id, index]));
  return items
    .map((item, index) => ({ item, index, rank: rank.get(idOf(item)) ?? saved.length + index }))
    .sort((a, b) => a.rank - b.rank)
    .map((entry) => entry.item);
}

/** Moves `id` to `target`'s place within `ids` (the rail as it is shown now). */
export function moveId(ids: string[], id: string, target: string): string[] {
  const from = ids.indexOf(id);
  const to = ids.indexOf(target);
  if (from < 0 || to < 0 || from === to) return ids;
  const next = ids.filter((entry) => entry !== id);
  next.splice(to, 0, id);
  return next;
}

export function useProviderOrder(): [string[], (shown: string[], id: string, target: string) => void] {
  const saved = useSyncExternalStore(subscribe, read, () => []);
  const move = useCallback((shown: string[], id: string, target: string) => {
    const moved = moveId(shown, id, target);
    // Keep the places of providers not on this rail (another picker's).
    write([...moved, ...read().filter((entry) => !moved.includes(entry))]);
  }, []);
  return [saved, move];
}
