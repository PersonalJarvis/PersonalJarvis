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
  /**
   * The assistant's answer while it is still being spoken / produced —
   * AssistantTextDelta snapshots from the voice paths, shown as a muted
   * live line under the lane until the spoken line (SpeechSpoken) lands.
   */
  liveReply: string;
  ingest: (name: string, payload: unknown, tsMs: number) => void;
  /**
   * Replace the lane with a stored conversation (a reopened voice session);
   * live events append after it.
   */
  seedTranscript: (lines: TranscriptLine[]) => void;
  /** Tests and a fresh session. */
  resetTranscript: () => void;
}

/**
 * The voice stage's live answer line: grows with every AssistantTextDelta
 * of a voice path, and goes away once the authoritative spoken line has
 * replaced it or the turn is over. Same string back for every other event.
 */
export function reduceLiveReply(current: string, name: string, payload: unknown): string {
  const p = (payload ?? {}) as Record<string, unknown>;
  switch (name) {
    case "AssistantTextDelta": {
      const channel = typeof p.channel === "string" ? p.channel : "";
      if (channel === "chat") return current;
      return typeof p.text === "string" ? p.text : current;
    }
    case "SpeechSpoken":
    case "MessageSent": {
      // The final words arrived as a transcript line — the preview has
      // served. (A preamble said mid-turn leaves the growing answer alone.)
      const kind = typeof p.spoken_kind === "string" ? p.spoken_kind : "";
      const role = typeof p.role === "string" ? p.role : "assistant";
      if (name === "MessageSent" && role !== "assistant") return current;
      if (name === "SpeechSpoken" && kind && kind !== "reply" && kind !== "other") {
        return current;
      }
      return "";
    }
    case "VoiceTurnCompleted":
      return "";
    case "SystemStateChanged": {
      const next = typeof p.new_state === "string" ? p.new_state.toLowerCase() : "";
      return next === "idle" ? "" : current;
    }
    default:
      return current;
  }
}

export const useHomeStore = create<HomeStore>((set, get) => ({
  surface: readHomeSurface(),
  setSurface: (surface) => {
    writeHomeSurface(surface);
    set({ surface });
  },
  transcript: [],
  liveReply: "",
  ingest: (name, payload, tsMs) => {
    const before = get().transcript;
    const after = reduceTranscript(before, name, payload, tsMs);
    const live = reduceLiveReply(get().liveReply, name, payload);
    if (after !== before || live !== get().liveReply) {
      set({ transcript: after, liveReply: live });
    }
  },
  seedTranscript: (lines) => set({ transcript: lines, liveReply: "" }),
  resetTranscript: () => set({ transcript: [], liveReply: "" }),
}));
