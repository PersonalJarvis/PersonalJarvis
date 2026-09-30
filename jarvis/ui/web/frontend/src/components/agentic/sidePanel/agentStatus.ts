import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";

/** The four colours an agent's dot can take — what a glance has to separate. */
export type AgentDotKind = "working" | "waiting" | "idle" | "error";

/** The finer reading behind a dot, shown as words. */
export type AgentStateKey =
  | "starting"
  | "working"
  | "needs_input"
  | "failed"
  | "error"
  | "exited"
  | "done"
  | "idle";

export function dotKindFor(pane: WorkspacePaneRow): AgentDotKind {
  if (pane.status === "error" || pane.activity === "failed") return "error";
  if (pane.activity === "asking") return "waiting";
  if (
    pane.activity === "working" ||
    pane.activity === "starting" ||
    pane.status === "pending"
  )
    return "working";
  return "idle";
}

export function stateKeyFor(pane: WorkspacePaneRow, kind: AgentDotKind = dotKindFor(pane)): AgentStateKey {
  if (kind === "working") return pane.activity === "starting" || pane.status === "pending" ? "starting" : "working";
  if (kind === "waiting") return "needs_input";
  if (kind === "error") return pane.activity === "failed" ? "failed" : "error";
  if (pane.status === "exited") return "exited";
  return pane.worked ? "done" : "idle";
}

export const DOT_STYLE: Record<AgentDotKind, { dot: string; ping: boolean }> = {
  working: { dot: "bg-success", ping: false },
  waiting: { dot: "bg-warning", ping: true },
  idle: { dot: "bg-muted-foreground/40", ping: false },
  error: { dot: "bg-destructive", ping: false },
};

/** Summary order: what needs the user first, then what is busy, then the rest. */
export const SUMMARY_ORDER: readonly AgentDotKind[] = ["waiting", "working", "error", "idle"];

/**
 * The coding agents of one workspace, in call-sign order.
 *
 * Shell panes are not agents and archived ones are hidden from every list —
 * the same filter the sidebar list used before it moved into the side panel.
 */
export function workspaceAgents(panes: WorkspacePaneRow[], workspaceId: string | null): WorkspacePaneRow[] {
  if (!workspaceId) return [];
  return panes
    .filter((pane) => pane.workspace_id === workspaceId && !pane.archived && pane.agent !== "shell")
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
}

/** "42s" / "3m" / "2h" / "1d" since an epoch-seconds moment; "" when unknown. */
export function compactSince(at: number | null | undefined, nowMs: number = Date.now()): string {
  if (!at) return "";
  const seconds = Math.max(0, Math.round(nowMs / 1000 - at));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86_400)}d`;
}

/** The three columns of the Agents tab, top to bottom. */
export type AgentColumn = "done" | "working" | "reviewed";

export const COLUMN_ORDER: readonly AgentColumn[] = ["done", "working", "reviewed"];

/**
 * Where one agent belongs.
 *
 * Working is the live reading. Anything settled — finished, asking, failed —
 * is Done until the user looks at it, and Reviewed once a look is newer than
 * the moment it settled (`activity_since`), so a reviewed agent that finishes
 * another job comes back to Done. A pane never given a job has nothing to
 * review and waits under Reviewed.
 */
export function columnFor(pane: WorkspacePaneRow, reviewedAt: number | undefined): AgentColumn {
  const kind = dotKindFor(pane);
  if (kind === "working") return "working";
  if (kind === "idle" && !pane.worked) return "reviewed";
  if (reviewedAt !== undefined && reviewedAt >= (pane.activity_since || 0)) return "reviewed";
  return "done";
}
