import { useEffect } from "react";
import { create } from "zustand";

import { fetchTerminalRecaps, type TerminalRecap } from "@/lib/agenticIdeApi";
import { useEventStore } from "@/store/events";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useWorkspacePanesStore } from "@/store/workspacePanes";
import { sessionTitle } from "@/components/agentic/sessionTitle";

/**
 * Every pane's short title — a few words naming its goal — for the workspace
 * on screen.
 *
 * The titles come from `/recaps`: the title the pane's coding CLI gave its
 * own session (`cli_title`), else one the user pinned, else — only when the
 * user switched model recaps on — a model-written label (`recap_engine`). The
 * grid headers and the side panel share one reference-counted poll here
 * rather than each running their own.
 *
 * It polls only while the Agentic IDE is the section on screen: the view stays
 * mounted when hidden, and a hidden workspace needs no fresh titles.
 */
const RECAP_POLL_MS = 8000;
/** Spread so several windows never fire on the same tick (AP-33). */
const RECAP_JITTER_MS = 1500;

type RecapsByName = Record<string, TerminalRecap>;

interface PaneRecapsState {
  /** The workspace the last answer was for, and its titles by pane name. */
  workspaceId: string | null;
  byName: RecapsByName;
  /**
   * Every workspace's last answer, kept after it leaves the front. Switching
   * back to a workspace then shows the titles it had at once instead of the
   * call-signs until the next poll (maintainer report 2026-10-05: "T1, T2" for
   * several seconds after every workspace switch).
   */
  byWorkspace: Record<string, RecapsByName>;
  load: (workspaceId?: string) => Promise<void>;
}

export const usePaneRecapsStore = create<PaneRecapsState>((set) => ({
  workspaceId: null,
  byName: {},
  byWorkspace: {},
  load: async (workspaceId) => {
    try {
      const answer = await fetchTerminalRecaps(workspaceId);
      if (!answer.workspace_id) return;
      const byName: RecapsByName = {};
      for (const row of answer.terminals) byName[row.name] = row;
      const id = answer.workspace_id;
      set((state) => ({
        workspaceId: id,
        byName,
        byWorkspace: { ...state.byWorkspace, [id]: byName },
      }));
    } catch {
      /* keep the last titles; the next tick tries again */
    }
  },
}));

/** The titles held for one workspace: the latest answer, else the cached one. */
export function recapsFor(
  state: Pick<PaneRecapsState, "workspaceId" | "byName" | "byWorkspace">,
  workspaceId: string | null | undefined,
): RecapsByName | undefined {
  if (!workspaceId) return state.byName;
  if (state.workspaceId === workspaceId) return state.byName;
  return state.byWorkspace?.[workspaceId];
}

let watchers = 0;
let timer: number | null = null;
let unsubscribe: (() => void) | null = null;

/** The workspace on screen, as the IDE published it; undefined before it has. */
function frontWorkspace(): string | undefined {
  return useIdeProjectsStore.getState().activeWorkspaceId ?? undefined;
}

function schedule(): void {
  if (timer !== null) window.clearTimeout(timer);
  timer = window.setTimeout(tick, RECAP_POLL_MS + Math.random() * RECAP_JITTER_MS);
}

function tick(): void {
  if (useEventStore.getState().activeSection === "agentic-ide") {
    void usePaneRecapsStore.getState().load(frontWorkspace());
  }
  schedule();
}

/**
 * Subscribe to the shared recap poll for as long as the caller is mounted.
 *
 * Besides the relaxed clock, the poll fires at once when the user brings a
 * workspace forward or opens the IDE: those are the moments a header shows a
 * pane whose title this window has not fetched yet, and waiting a whole
 * interval left it on its call-sign. A user's own click is one request, not a
 * reconnect wave, so it needs no jitter; the clock restarts from it.
 */
export function usePaneRecapPoll(): void {
  useEffect(() => {
    watchers += 1;
    if (timer === null) tick();
    if (unsubscribe === null) {
      const offWorkspace = useIdeProjectsStore.subscribe((state, previous) => {
        if (state.activeWorkspaceId !== previous.activeWorkspaceId && state.activeWorkspaceId) tick();
      });
      const offSection = useEventStore.subscribe((state, previous) => {
        if (state.activeSection === "agentic-ide" && previous.activeSection !== "agentic-ide") tick();
      });
      unsubscribe = () => {
        offWorkspace();
        offSection();
      };
    }
    return () => {
      watchers -= 1;
      if (watchers <= 0) {
        if (timer !== null) window.clearTimeout(timer);
        timer = null;
        unsubscribe?.();
        unsubscribe = null;
      }
    };
  }, []);
}

/**
 * The words a pane is labelled with instead of its call-sign.
 *
 * A title the user pinned, the CLI gave its session, or the model wrote wins.
 * Before one exists, the pane's own topic (`sessionTitle`: its last prompt, the message that opened
 * the conversation) stands in; a pane that was never asked anything has no
 * topic, and "" tells the caller to fall back to the call-sign.
 */
export function paneTitleFrom(
  recap: Pick<TerminalRecap, "recap" | "source"> | undefined,
  row: Parameters<typeof sessionTitle>[0] | undefined,
): string {
  const written = (recap?.recap ?? "").trim();
  const named = recap?.source === "user" || recap?.source === "cli" || recap?.source === "model";
  if (written && named) return written;
  if (!row) return "";
  const topic = sessionTitle(row).trim();
  return topic && topic !== row.display_name && topic !== row.name ? topic : "";
}

/** One pane's title, live: the shared recap poll plus the pane list's topic. */
export function usePaneTitle(workspaceId: string | undefined, name: string): string {
  usePaneRecapPoll();
  const recap = usePaneRecapsStore((state) => recapsFor(state, workspaceId)?.[name]);
  const row = useWorkspacePanesStore((state) =>
    state.panes.find((pane) => pane.name === name && (!workspaceId || pane.workspace_id === workspaceId)),
  );
  return paneTitleFrom(recap, row);
}
