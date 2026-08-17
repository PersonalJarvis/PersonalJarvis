import { create } from "zustand";
import { emptyDeckState, reduceDeck, type DeckState } from "@/lib/deckState";
import { readDeckMode, writeDeckMode, type DeckMode } from "@/lib/deckMode";

/**
 * The mission deck's own store: what its cards show, and which surface the
 * "chats" section is on.
 *
 * Fed from the WebSocket hook with one `ingest` call per event, the same way
 * the command-activity and thinking-trace stores are. Kept apart from the big
 * event store because none of this is read anywhere else — and because the
 * reducer returns the same object for the (vast) majority of events, which
 * `set` turns into a no-op without waking a single subscriber.
 *
 * The surface mode lives here rather than in a view's local state so the app
 * shell can read it: on the deck the sidebar gives way to the deck's own dock
 * (App.tsx), and a view cannot tell the shell that from the inside.
 */
interface DeckStore extends DeckState {
  mode: DeckMode;
  setMode: (mode: DeckMode) => void;
  ingest: (name: string, payload: unknown, tsMs: number) => void;
  /** Tests and a fresh session — forget everything but the surface choice. */
  resetDeck: () => void;
}

export const useDeckStore = create<DeckStore>((set, get) => ({
  ...emptyDeckState(),
  mode: readDeckMode(),

  setMode: (mode) => {
    writeDeckMode(mode);
    set({ mode });
  },

  ingest: (name, payload, tsMs) => {
    const before = get();
    const after = reduceDeck(before, name, payload, tsMs);
    if (after !== before) set(after);
  },

  resetDeck: () => set(emptyDeckState()),
}));
