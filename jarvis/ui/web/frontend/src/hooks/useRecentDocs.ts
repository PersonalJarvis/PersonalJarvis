import { useCallback, useEffect, useState } from "react";

import type { DocDiataxis } from "@/hooks/useDocs";

const STORAGE_KEY = "docs.recent.v1";
const MAX_ENTRIES = 5;
const CHANGE_EVENT = "docs-recent-changed";

export interface RecentDoc {
  slug: string;
  title: string;
  diataxis: DocDiataxis;
  /** Unix-ms — fuer LRU-Sortierung. */
  openedAt: number;
}

/**
 * localStorage-basierter Recent-Docs-Tracker.
 *
 * Eintraege werden im LRU-Stil gehalten: beim Doc-Open wird der Eintrag an
 * die Spitze geschoben (oder neu angelegt). Cap bei ``MAX_ENTRIES`` (5);
 * laengere Listen wuerden in der Sidebar zu viel Platz brauchen.
 *
 * State + localStorage werden synchron gehalten — kein react-query, weil
 * die Daten ohnehin client-only sind.
 */
export function useRecentDocs(): {
  recent: RecentDoc[];
  push: (doc: Omit<RecentDoc, "openedAt">) => void;
  clear: () => void;
} {
  const [recent, setRecent] = useState<RecentDoc[]>(() => loadFromStorage());

  // Sync-Pattern fuer mehrere ``useRecentDocs``-Instanzen im selben Window:
  // ``storage``-Event firet nur cross-tab, nicht intra-tab. Custom-Event
  // ``docs-recent-changed`` wird bei jedem ``push``/``clear`` dispatched
  // und alle Instanzen reloaden aus localStorage.
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
