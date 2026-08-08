import { create } from "zustand";
import {
  pruneCommandActivity,
  reduceCommandActivity,
  type CommandActivityEntry,
} from "@/lib/commandActivity";

/**
 * Live shell-command activity, fed by the WS wildcard event stream
 * (useWebSocket.ts) and rendered by CommandActivityLayer.tsx.
 *
 * Deliberately its own store: the entries change at tool-call rhythm, and
 * keeping them out of the big event store means a running command re-renders
 * two activity cards, not the whole shell.
 */
interface CommandActivityState {
  entries: CommandActivityEntry[];
  ingest: (
    eventName: string,
    traceId: string,
    payload: unknown,
    tsMs: number,
  ) => void;
  /** Called by the layer's 1 s tick — expires settled/stuck cards. */
  prune: (nowMs: number) => void;
  dismiss: (id: string) => void;
}

export const useCommandActivityStore = create<CommandActivityState>()(
  (set, get) => ({
    entries: [],

    ingest: (eventName, traceId, payload, tsMs) => {
      const next = reduceCommandActivity(
        get().entries,
        eventName,
        traceId,
        payload,
        tsMs,
      );
      if (next) set({ entries: next });
    },

    prune: (nowMs) => {
      const next = pruneCommandActivity(get().entries, nowMs);
      if (next) set({ entries: next });
    },

    dismiss: (id) =>
      set((state) => ({
        entries: state.entries.filter((e) => e.id !== id),
      })),
  }),
);

/** The event names the WS hook needs to forward here (cheap pre-filter). */
export const COMMAND_ACTIVITY_EVENTS: ReadonlySet<string> = new Set([
  "ActionProposed",
  "ActionExecuted",
  "ActionDenied",
]);
