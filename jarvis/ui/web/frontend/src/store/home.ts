import { create } from "zustand";

import { readHomeSurface, writeHomeSurface, type HomeSurface } from "@/lib/homeSurface";
import { reduceTranscript, type TranscriptLine } from "@/lib/homeTranscript";

/**
 * The front page's own store: which surface (voice / chat) is on screen, and
 * the live transcript the voice stage shows.
 *
 * Lives apart from the big event store because it is read by exactly two
 * places — the sidebar switch that sets it and the home view that renders
 * it. The transcript is fed from the WebSocket hook with one `ingest` call
 * per event, the same way the deck and command-activity stores are; the
 * reducer returns the same array for the (vast) majority of events, which
 * `set` turns into a no-op without waking a single subscriber.
 */
interface HomeStore {
  surface: HomeSurface;
  setSurface: (surface: HomeSurface) => void;
  /** What was said and answered, oldest first (lib/homeTranscript.ts). */
  transcript: TranscriptLine[];
  ingest: (name: string, payload: unknown, tsMs: number) => void;
  /** Tests and a fresh session. */
  resetTranscript: () => void;
}

export const useHomeStore = create<HomeStore>((set, get) => ({
  surface: readHomeSurface(),
  setSurface: (surface) => {
    writeHomeSurface(surface);
    set({ surface });
  },
  transcript: [],
  ingest: (name, payload, tsMs) => {
    const before = get().transcript;
    const after = reduceTranscript(before, name, payload, tsMs);
    if (after !== before) set({ transcript: after });
  },
  resetTranscript: () => set({ transcript: [] }),
}));
