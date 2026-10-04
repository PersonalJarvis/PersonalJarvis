import { useCallback, useSyncExternalStore } from "react";
import { requestConnect, spreadDelay } from "@/lib/connectBudget";
import type { CiStatus, PullRequestState } from "./sidePanel/git/gitOverviewApi";

export interface SessionGitHubStatus {
  repo: string;
  branch: string;
  url: string;
  published: boolean;
  owned: boolean;
  available: boolean;
  reason: string;
  fetched_at: number;
  state: "branch" | PullRequestState;
  number: number | null;
  ci: Omit<CiStatus, "state"> & { state: SessionCiState; states?: SessionCiState[] };
  ci_stale: boolean;
  merge_status?: string;
  review?: string;
  locked?: boolean;
}

export type SessionCiState = CiStatus["state"] | "cancelled" | "neutral" | "skipped"
  | "action_required" | "timed_out" | "startup_failure" | "stale" | "unknown"
  | "waiting" | "requested" | "expected" | "error";

type Panes = Record<string, SessionGitHubStatus | null>;
type Entry = {
  panes: Panes;
  listeners: Set<() => void>;
  stop: () => void;
};
const entries = new Map<string, Entry>();
const EMPTY: Panes = {};

function start(workspaceId: string): Entry {
  const entry: Entry = { panes: EMPTY, listeners: new Set(), stop: () => {} };
  let disposed = false;
  let controller: AbortController | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancelConnect: (() => void) | undefined;

  const notify = () => entry.listeners.forEach((listener) => listener());
  const unavailable = (reason: string) => {
    entry.panes = Object.fromEntries(Object.entries(entry.panes).map(([name, status]) => [
      name, status ? { ...status, available: false, reason } : null,
    ]));
    notify();
  };
  const schedule = (delay: number) => {
    clearTimeout(timer);
    cancelConnect?.();
    if (disposed || document.hidden) return;
    cancelConnect = requestConnect(() => { void poll(); }, delay);
  };
  const poll = async () => {
    if (disposed || controller || document.hidden) return;
    controller = new AbortController();
    const timeout = setTimeout(() => controller?.abort(), 30_000);
    try {
      const query = new URLSearchParams({ workspace_id: workspaceId });
      const response = await fetch(`/api/agentic-ide/git/session-status?${query}`, { signal: controller.signal });
      if (!response.ok) throw new Error("GitHub status could not be refreshed.");
      const data = await response.json() as { panes: Panes };
      if (disposed) return;
      // After logout/offline, keep a previously confirmed branch visible with
      // an unknown status. Never borrow identity across a checkout change.
      entry.panes = Object.fromEntries(Object.entries(data.panes).map(([name, status]) => {
        const previous = entry.panes[name];
        if (status?.owned && !status.available && previous?.owned && previous.published
          && status.repo === previous.repo && status.branch === previous.branch) {
          return [name, { ...status, published: true }];
        }
        return [name, status];
      }));
      notify();
    } catch {
      if (!disposed) unavailable("GitHub status could not be refreshed.");
    } finally {
      clearTimeout(timeout);
      controller = undefined;
      if (!disposed) timer = setTimeout(() => schedule(0), spreadDelay(20_000));
    }
  };
  const resume = () => {
    if (document.hidden) {
      clearTimeout(timer);
      cancelConnect?.();
      return;
    }
    // A suspended window must not present its last green badge as live data.
    if (Object.values(entry.panes).some((value) => value && Date.now() / 1000 - value.fetched_at > 60)) {
      unavailable("Refreshing GitHub status…");
    }
    if (!controller) schedule(spreadDelay(500));
  };
  document.addEventListener("visibilitychange", resume);
  window.addEventListener("online", resume);
  entry.stop = () => {
    disposed = true;
    clearTimeout(timer);
    cancelConnect?.();
    controller?.abort();
    document.removeEventListener("visibilitychange", resume);
    window.removeEventListener("online", resume);
  };
  schedule(spreadDelay(250));
  return entry;
}

export function useSessionGitHub(workspaceId?: string, pane?: string): SessionGitHubStatus | null {
  const subscribe = useCallback((listener: () => void) => {
    if (!workspaceId) return () => {};
    let entry = entries.get(workspaceId);
    if (!entry) {
      entry = start(workspaceId);
      entries.set(workspaceId, entry);
    }
    entry.listeners.add(listener);
    return () => {
      entry.listeners.delete(listener);
      if (!entry.listeners.size) {
        entry.stop();
        entries.delete(workspaceId);
      }
    };
  }, [workspaceId]);
  const snapshot = useCallback(() => workspaceId ? entries.get(workspaceId)?.panes ?? EMPTY : EMPTY, [workspaceId]);
  const panes = useSyncExternalStore(subscribe, snapshot, () => EMPTY);
  return pane ? panes[pane] ?? null : null;
}
