import { useCallback, useEffect, useState } from "react";

import type { DocDiataxis } from "@/hooks/useDocs";

const STORAGE_KEY = "docs.recent.v1";
const MAX_ENTRIES = 5;
const CHANGE_EVENT = "docs-recent-changed";

export interface RecentDoc {
  slug: string;
  title: string;
  diataxis: DocDiataxis;
  /** Unix ms — for LRU sorting. */
  openedAt: number;
}

/**
 * localStorage-based recent-docs tracker.
 *
 * Entries are kept LRU-style: opening a doc moves the entry to the top
 * (or creates it). Capped at ``MAX_ENTRIES`` (5); longer lists would take
 * up too much room in the sidebar.
 *
 * State + localStorage are kept in sync — no react-query, since the data
 * is client-only anyway.
 */
export function useRecentDocs(): {
  recent: RecentDoc[];
  push: (doc: Omit<RecentDoc, "openedAt">) => void;
  clear: () => void;
} {
  const [recent, setRecent] = useState<RecentDoc[]>(() => loadFromStorage());

  // Sync pattern for multiple ``useRecentDocs`` instances in the same window:
  // the ``storage`` event only fires cross-tab, not intra-tab. The custom
  // ``docs-recent-changed`` event is dispatched on every ``push``/``clear``
  // and all instances reload from localStorage.
  useEffect(() => {
    const reload = () => setRecent(loadFromStorage());
    window.addEventListener("storage", (e) => {
      if (e.key === STORAGE_KEY) reload();
    });
    window.addEventListener(CHANGE_EVENT, reload);
    return () => {
      window.removeEventListener(CHANGE_EVENT, reload);
    };
  }, []);

  const push = useCallback((doc: Omit<RecentDoc, "openedAt">) => {
    setRecent((prev) => {
      const filtered = prev.filter((d) => d.slug !== doc.slug);
      const next: RecentDoc[] = [
        { ...doc, openedAt: Date.now() },
        ...filtered,
      ].slice(0, MAX_ENTRIES);
      saveToStorage(next);
      return next;
    });
    // Andere ``useRecentDocs``-Instanzen im selben Window benachrichtigen.
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
  }, []);

  const clear = useCallback(() => {
    setRecent([]);
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* Quota / private mode — silent ignore */
    }
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
  }, []);

  return { recent, push, clear };
}

function loadFromStorage(): RecentDoc[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (e) =>
          e &&
          typeof e === "object" &&
          typeof e.slug === "string" &&
          typeof e.title === "string",
      )
      .slice(0, MAX_ENTRIES);
  } catch {
    return [];
  }
}

function saveToStorage(entries: RecentDoc[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    /* Quota / private mode — silent ignore */
  }
}
