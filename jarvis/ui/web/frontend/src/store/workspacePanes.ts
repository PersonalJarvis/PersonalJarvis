import { useEffect } from "react";
import { create } from "zustand";

import {
  fetchWorkspacePanes,
  type WorkspacePaneRow,
} from "@/lib/agenticIdeApi";

/**
 * Every coding session running in any open workspace, kept fresh in one place.
 *
 * Two views ask the same question at the same time — the chat sidebar's session
 * list and the session page it opens — and both want it to stay current, since
 * the whole point of the list is that a spinner turns into a dot when an agent
 * finishes. One store with ONE poll behind it, rather than a timer per view:
 * the second timer would double the request rate for a payload both are already
 * holding, and the two copies would disagree for up to a poll interval, which
 * is exactly the "working here, done there" split this list exists to end.
 *
 * The poll is reference-counted: it starts when the first view subscribes and
 * stops when the last one leaves, so nothing is polled while nobody is looking.
 */
const REFRESH_MS = 4000;

interface WorkspacePanesStore {
  panes: WorkspacePaneRow[];
  /** The workspace tab at the front, as the backend sees it. */
  activeId: string | null;
  /** Has a first answer arrived? An empty list before that means "not yet". */
  loaded: boolean;
  load: () => Promise<void>;
}

export const useWorkspacePanesStore = create<WorkspacePanesStore>((set) => ({
  panes: [],
  activeId: null,
  loaded: false,

  load: async () => {
    try {
      const result = await fetchWorkspacePanes();
      set({ panes: result.panes, activeId: result.active_id, loaded: true });
    } catch {
      // Offline, headless, or the workspace registry not up yet. The list keeps
      // what it had: a sidebar that empties itself on one failed poll would
      // read as "your agents are gone".
    }
  },
}));

let watchers = 0;
let timer: number | null = null;

/**
 * Subscribe this component to the pane list, sharing one poll with the others.
 *
 * Returns the rows so a caller can use it as a plain hook.
 */
export function useWorkspacePanes(): WorkspacePaneRow[] {
  const panes = useWorkspacePanesStore((s) => s.panes);
  useEffect(() => {
    watchers += 1;
    if (timer === null) {
      void useWorkspacePanesStore.getState().load();
      timer = window.setInterval(() => {
        void useWorkspacePanesStore.getState().load();
      }, REFRESH_MS);
    }
    return () => {
      watchers -= 1;
      if (watchers <= 0 && timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    };
  }, []);
  return panes;
}

/** Test seam: forget the shared timer between cases. */
export function resetWorkspacePanesPoll(): void {
  if (timer !== null) window.clearInterval(timer);
  timer = null;
  watchers = 0;
}
