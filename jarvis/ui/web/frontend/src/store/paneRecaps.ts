import { useEffect } from "react";
import { create } from "zustand";

import { fetchTerminalRecaps, type TerminalRecap } from "@/lib/agenticIdeApi";
import { useEventStore } from "@/store/events";
import { useWorkspacePanesStore } from "@/store/workspacePanes";
import { sessionTitle } from "@/components/agentic/sessionTitle";

/**
 * Every pane's short title — a few words naming its goal — for the workspace
 * on screen.
 *
 * The titles come from `/recaps`: a model reads each pane and writes a 3–5
 * word navigation label (`recap_engine`). That poll is also the ONLY thing
 * that asks the backend to write those labels at all — the summarizer runs
 * because somebody is looking — so the grid headers and the side panel share
 * one reference-counted poll here rather than each running their own.
 *
 * It polls only while the Agentic IDE is the section on screen: the view stays
 * mounted when hidden, and a hidden workspace needs no fresh titles.
 */
const RECAP_POLL_MS = 8000;
/** Spread so several windows never fire on the same tick (AP-33). */
const RECAP_JITTER_MS = 1500;

interface PaneRecapsState {
  workspaceId: string | null;
  byName: Record<string, TerminalRecap>;
  load: () => Promise<void>;
}

export const usePaneRecapsStore = create<PaneRecapsState>((set) => ({
  workspaceId: null,
  byName: {},
  load: async () => {
    try {
      const answer = await fetchTerminalRecaps();
      const byName: Record<string, TerminalRecap> = {};
      for (const row of answer.terminals) byName[row.name] = row;
      set({ workspaceId: answer.workspace_id, byName });
    } catch {
      /* keep the last titles; the next tick tries again */
    }
  },
}));

let watchers = 0;
let timer: number | null = null;

function tick(): void {
  if (useEventStore.getState().activeSection === "agentic-ide") {
    void usePaneRecapsStore.getState().load();
  }
  timer = window.setTimeout(tick, RECAP_POLL_MS + Math.random() * RECAP_JITTER_MS);
}

/** Subscribe to the shared recap poll for as long as the caller is mounted. */
export function usePaneRecapPoll(): void {
  useEffect(() => {
    watchers += 1;
    if (timer === null) tick();
    return () => {
      watchers -= 1;
      if (watchers <= 0 && timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };
  }, []);
}

/**
 * The words a pane is labelled with instead of its call-sign.
 *
 * A title the user pinned or the model wrote wins. Before one exists, the
 * pane's own topic (`sessionTitle`: its last prompt, the message that opened
 * the conversation) stands in; a pane that was never asked anything has no
 * topic, and "" tells the caller to fall back to the call-sign.
 */
export function paneTitleFrom(
  recap: Pick<TerminalRecap, "recap" | "source"> | undefined,
  row: Parameters<typeof sessionTitle>[0] | undefined,
): string {
  const written = (recap?.recap ?? "").trim();
  if (written && (recap?.source === "model" || recap?.source === "user")) return written;
  if (!row) return "";
  const topic = sessionTitle(row).trim();
  return topic && topic !== row.display_name && topic !== row.name ? topic : "";
}

/** One pane's title, live: the shared recap poll plus the pane list's topic. */
export function usePaneTitle(workspaceId: string | undefined, name: string): string {
  usePaneRecapPoll();
  const recap = usePaneRecapsStore((state) =>
    !workspaceId || state.workspaceId === workspaceId ? state.byName[name] : undefined,
  );
  const row = useWorkspacePanesStore((state) =>
    state.panes.find((pane) => pane.name === name && (!workspaceId || pane.workspace_id === workspaceId)),
  );
  return paneTitleFrom(recap, row);
}
