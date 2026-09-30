// Read-only screen snapshots of Agentic-IDE panes — what the office's desk
// monitors draw. Plain same-origin fetch like agenticIdeApi, `no-store` because
// WebView2 would otherwise serve a stale screen from cache.

export interface PaneScreen {
  workspace_id: string;
  key: string;
  name: string;
  cols: number;
  rows: number;
  /** Visible rows, right-trimmed; at most 60 rows of 240 characters. */
  lines: string[];
  /** `[row, col]` into `lines`, or null when the agent hides its cursor. */
  cursor: [number, number] | null;
  /** Last output time (epoch seconds) — changes exactly when the screen can. */
  at: number;
}

/** The server reads at most this many panes per call. */
export const MAX_PANE_SCREENS = 8;

/**
 * Screens for the given panes. Unknown panes are simply missing from the
 * answer; more than {@link MAX_PANE_SCREENS} are cut here rather than by the
 * server.
 */
export async function fetchPaneScreens(
  panes: { workspaceId: string; key: string }[],
): Promise<PaneScreen[]> {
  if (panes.length === 0) return [];
  const query = new URLSearchParams();
  for (const pane of panes.slice(0, MAX_PANE_SCREENS)) {
    query.append("pane", `${pane.workspaceId}:${pane.key}`);
  }
  const res = await fetch(`/api/agentic-ide/screens?${query}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`pane screens failed: HTTP ${res.status}`);
  const body = (await res.json()) as { screens?: PaneScreen[] };
  return Array.isArray(body.screens) ? body.screens : [];
}
