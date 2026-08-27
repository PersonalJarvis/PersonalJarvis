import { useEffect } from "react";
import { create } from "zustand";

import {
  fetchWorkspacePanes,
  type PaneActivity,
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
 *
 * ## The poll is the fallback; the socket is the feed
 *
 * A list that only polls is a list that is wrong for up to a poll interval
 * after every change — a session that finished kept its spinner for as long
 * as four seconds, and with twenty sessions in twenty states something was
 * always lagging (maintainer report 2026-08-27). The backend sweep that
 * decides each pane's word now says so on the app socket the moment it does
 * (`AgenticIdePaneActivity`, carried here as `PANE_ACTIVITY_EVENT`), and the
 * store patches the row in place. The poll stays for what the event does not
 * carry — a pane appearing, a title changing — and as the answer for a socket
 * that dropped.
 */
const REFRESH_MS = 4000;

/**
 * The window event `useWebSocket` raises for one pane's activity change.
 *
 * Its detail is the activity half of a `WorkspacePaneRow`, keyed by workspace
 * and by the pane's stable call-sign — the same shape the backend event
 * carries, so nothing is translated on the way.
 */
export const PANE_ACTIVITY_EVENT = "jarvis:agentic-ide-activity";

/** What one `PANE_ACTIVITY_EVENT` carries: exactly the backend's payload. */
export interface PaneActivityChange {
  session_id: string;
  key: string;
  name: string;
  status: string;
  activity: PaneActivity;
  activity_since: number;
  worked: boolean;
}

/** The change, when the event carries one; null for a malformed detail. */
export function readPaneActivityChange(detail: unknown): PaneActivityChange | null {
  if (!detail || typeof detail !== "object") return null;
  const p = detail as Partial<PaneActivityChange>;
  if (typeof p.session_id !== "string" || typeof p.key !== "string") return null;
  return {
    session_id: p.session_id,
    key: p.key,
    name: typeof p.name === "string" ? p.name : "",
    status: typeof p.status === "string" ? p.status : "",
    activity: (typeof p.activity === "string" ? p.activity : "") as PaneActivity,
    activity_since: typeof p.activity_since === "number" ? p.activity_since : 0,
    worked: p.worked === true,
  };
}

interface WorkspacePanesStore {
  panes: WorkspacePaneRow[];
  /** The workspace tab at the front, as the backend sees it. */
  activeId: string | null;
  /** Has a first answer arrived? An empty list before that means "not yet". */
  loaded: boolean;
  load: () => Promise<void>;
  /**
   * Patch one row with a change the socket delivered. A row this store does
   * not hold — a pane the last poll had not listed yet — is left for the next
   * poll rather than invented from a payload that carries no title or agent.
   */
  applyActivity: (change: PaneActivityChange) => void;
}

const STATUSES: ReadonlySet<string> = new Set(["pending", "live", "exited", "error"]);

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

  applyActivity: (change) =>
    set((state) => {
      const index = state.panes.findIndex(
        (pane) => pane.workspace_id === change.session_id && pane.key === change.key,
      );
      if (index < 0) return state;
      const current = state.panes[index];
      const status = STATUSES.has(change.status)
        ? (change.status as WorkspacePaneRow["status"])
        : current.status;
      if (
        current.status === status &&
        current.activity === change.activity &&
        current.activity_since === change.activity_since &&
        current.worked === change.worked
      ) {
        // The poll already said so. Keep the array's identity, or every
        // repeated event would redraw a list nothing changed in.
        return state;
      }
      const panes = state.panes.slice();
      panes[index] = {
        ...current,
        status,
        activity: change.activity,
        activity_since: change.activity_since,
        worked: change.worked,
      };
      return { panes };
    }),
}));

let watchers = 0;
let timer: number | null = null;
let onActivity: ((event: Event) => void) | null = null;

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
    if (onActivity === null) {
      // One listener for the whole store, alive exactly as long as the poll:
      // the socket feed and the poll are two sources for one list, and they
      // start and stop together.
      onActivity = (event: Event) => {
        const change = readPaneActivityChange((event as CustomEvent).detail);
        if (change) useWorkspacePanesStore.getState().applyActivity(change);
      };
      window.addEventListener(PANE_ACTIVITY_EVENT, onActivity);
    }
    return () => {
      watchers -= 1;
      if (watchers <= 0) {
        if (timer !== null) window.clearInterval(timer);
        timer = null;
        if (onActivity !== null) window.removeEventListener(PANE_ACTIVITY_EVENT, onActivity);
        onActivity = null;
      }
    };
  }, []);
  return panes;
}

/** Patch one row in place — archive, restore — without waiting for the poll. */
export function patchWorkspacePane(
  historyId: string,
  patch: Partial<WorkspacePaneRow>,
): void {
  useWorkspacePanesStore.setState((state) => {
    const index = state.panes.findIndex((pane) => pane.history_id === historyId);
    if (index < 0) return state;
    const panes = state.panes.slice();
    panes[index] = { ...panes[index], ...patch };
    return { panes };
  });
}

/** Drop a row the moment its terminal is closed, so the list does not wait. */
export function dropWorkspacePane(historyId: string): void {
  useWorkspacePanesStore.setState((state) => {
    const panes = state.panes.filter((pane) => pane.history_id !== historyId);
    return panes.length === state.panes.length ? state : { panes };
  });
}

/** Test seam: forget the shared timer between cases. */
export function resetWorkspacePanesPoll(): void {
  if (timer !== null) window.clearInterval(timer);
  timer = null;
  if (onActivity !== null) window.removeEventListener(PANE_ACTIVITY_EVENT, onActivity);
  onActivity = null;
  watchers = 0;
}
