import { useCallback, useMemo } from "react";
import { create } from "zustand";
import type { TimelineItem } from "@/components/agentchat/reduce";

const STORAGE_KEY = "jarvis.chatView.cleared.v1";
interface ViewBoundary { id: string; tsMs: number }
type Boundaries = Record<string, ViewBoundary>;

export function readClearedViews(): Boundaries {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
    return Object.fromEntries(Object.entries(raw).filter(([, value]) =>
      value && typeof value.id === "string" && typeof value.tsMs === "number" && Number.isFinite(value.tsMs),
    ));
  } catch {
    // A corrupt or unavailable browser preference must not prevent opening a chat.
    return {};
  }
}

/** Only display boundaries are stored here; the chat store and server history stay intact. */
export const useTranscriptViewStore = create<{ boundaries: Boundaries }>(() => ({
  boundaries: readClearedViews(),
}));

function timestamp(item: TimelineItem): number {
  return item.type === "turn" ? item.startedMs : item.tsMs;
}

function afterBoundary(items: TimelineItem[], boundary: ViewBoundary | undefined): TimelineItem[] {
  if (!boundary) return items;
  const at = items.findIndex((item) => item.id === boundary.id);
  // The item ID preserves ordering even when several messages share a timestamp.
  if (at >= 0) return items.slice(at + 1);
  // A reconnect can temporarily provide only a partial snapshot.
  return items.filter((item) => timestamp(item) > boundary.tsMs);
}

export function useTranscriptView(sessionId: string | null, items: TimelineItem[]) {
  const boundary = useTranscriptViewStore((s) => sessionId ? s.boundaries[sessionId] : undefined);
  const visibleItems = useMemo(() => afterBoundary(items, boundary), [items, boundary]);
  const clear = useCallback(() => {
    const last = items.at(-1);
    if (!sessionId || !last) return;
    useTranscriptViewStore.setState((state) => ({
      boundaries: { ...state.boundaries, [sessionId]: { id: last.id, tsMs: timestamp(last) } },
    }));
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(useTranscriptViewStore.getState().boundaries));
    } catch {
      // Clearing still works for this app session when browser storage is disabled/full.
    }
  }, [sessionId, items]);
  return { items: visibleItems, clear, boundaryId: boundary?.id ?? "" };
}
