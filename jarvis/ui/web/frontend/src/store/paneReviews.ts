import { useEffect } from "react";
import { create } from "zustand";

import { useIdeChatStore } from "@/store/ideChat";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useWorkspacePanesStore } from "@/store/workspacePanes";

/**
 * Which finished agents the user has already looked at.
 *
 * The Agents tab sorts every coding agent into Done, Working and Reviewed. A
 * finished agent moves from Done to Reviewed the moment its pane is clicked —
 * in the grid or on its card — and back to Done when it finishes a NEW job
 * (its settled-since stamp is then newer than the review). Kept per pane
 * lifetime id (`history_id`, stable across renames and restarts) with the
 * moment of the look, in localStorage so a reload or an app restart does not
 * put everything back under Done. Storage may be blocked; then the reviews
 * last for this session only.
 */
const STORAGE_KEY = "jarvis.agenticIde.paneReviews.v1";
/** Old entries are dropped beyond this many panes. */
const MAX_ENTRIES = 400;

function load(): Record<string, number> {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") as unknown;
    if (!raw || typeof raw !== "object") return {};
    const out: Record<string, number> = {};
    for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
      if (typeof value === "number" && Number.isFinite(value)) out[key] = value;
    }
    return out;
  } catch {
    return {};
  }
}

function save(reviewed: Record<string, number>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(reviewed));
  } catch {
    /* a convenience only: reviews then last for this session */
  }
}

interface PaneReviewsState {
  /** history_id → epoch seconds of the last look. */
  reviewed: Record<string, number>;
  markReviewed: (historyId: string, atSeconds?: number) => void;
}

export const usePaneReviewsStore = create<PaneReviewsState>((set, get) => ({
  reviewed: load(),
  markReviewed: (historyId, atSeconds = Date.now() / 1000) => {
    if (!historyId) return;
    const next = { ...get().reviewed, [historyId]: atSeconds };
    const keys = Object.keys(next);
    if (keys.length > MAX_ENTRIES) {
      keys
        .sort((a, b) => next[a] - next[b])
        .slice(0, keys.length - MAX_ENTRIES)
        .forEach((key) => delete next[key]);
    }
    set({ reviewed: next });
    save(next);
  },
}));

/** Mark the pane of `workspaceId` called `name` as looked at, if it is known. */
export function markPaneReviewed(workspaceId: string | null, name: string | null): void {
  if (!workspaceId || !name) return;
  const pane = useWorkspacePanesStore
    .getState()
    .panes.find((row) => row.workspace_id === workspaceId && row.name === name);
  if (pane) usePaneReviewsStore.getState().markReviewed(pane.history_id);
}

/**
 * Count a click on a pane in the grid as reviewing it.
 *
 * The grid reports its selected pane as `stagedPane`; every change of it is a
 * pane the user just clicked or focused. Mounted once, beside the grid.
 */
export function usePaneReviewTracking(): void {
  const stagedPane = useIdeChatStore((state) => state.stagedPane);
  const workspaceId = useIdeProjectsStore((state) => state.activeWorkspaceId);
  useEffect(() => {
    markPaneReviewed(workspaceId, stagedPane);
  }, [stagedPane, workspaceId]);
}
