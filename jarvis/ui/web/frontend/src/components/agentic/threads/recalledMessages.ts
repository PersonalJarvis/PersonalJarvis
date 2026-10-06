import { create } from "zustand";
import type { TimelineItem } from "@/components/agentchat/reduce";

/**
 * Messages the person took back with Escape. The transcript keeps them (the
 * event log is append-only), so the thread hides each one, and the stopped
 * turn that answered it, by the user item's id — stable across reloads,
 * because it comes from the event's seq.
 */

const STORAGE_KEY = "jarvis.threads.recalled";
/** Enough for any thread; the oldest sessions drop out first. */
const SESSION_LIMIT = 200;

type Recalled = Record<string, string[]>;

function load(): Recalled {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Recalled : {};
  } catch {
    // Unreadable or blocked storage: nothing is hidden, the thread still works.
    return {};
  }
}

function save(next: Recalled): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Storage full or blocked: the message stays hidden until the next reload only.
  }
}

export const useRecalledMessages = create<{ bySession: Recalled; hide: (sessionId: string, userItemId: string) => void }>((set, get) => ({
  bySession: load(),
  hide: (sessionId, userItemId) => {
    const current = get().bySession;
    const ids = current[sessionId] ?? [];
    if (ids.includes(userItemId)) return;
    const { [sessionId]: _drop, ...rest } = current;
    const entries = Object.entries({ ...rest, [sessionId]: [...ids, userItemId] });
    const next = Object.fromEntries(entries.slice(-SESSION_LIMIT));
    save(next);
    set({ bySession: next });
  },
}));

/** The timeline without the taken-back messages and whatever answered them. */
export function withoutRecalled<T extends Pick<TimelineItem, "type" | "id">>(items: T[], hidden: readonly string[] | undefined): T[] {
  if (!hidden?.length) return items;
  const out: T[] = [];
  let skipping = false;
  for (const item of items) {
    if (item.type === "user") skipping = hidden.includes(item.id);
    if (!skipping) out.push(item);
  }
  return out;
}
