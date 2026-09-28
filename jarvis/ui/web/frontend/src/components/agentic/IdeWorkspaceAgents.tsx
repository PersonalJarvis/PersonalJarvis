import { useIdeChatStore } from "@/store/ideChat";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useWorkspacePanes } from "@/store/workspacePanes";
import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";
import { cn } from "@/lib/utils";

type AgentDotKind = "working" | "waiting" | "idle" | "error";

function dotKindFor(pane: WorkspacePaneRow): AgentDotKind {
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

const DOT_STYLE: Record<AgentDotKind, { dot: string; label: string; ping: boolean }> = {
  working: { dot: "bg-success", label: "working", ping: false },
  waiting: { dot: "bg-warning", label: "needs input", ping: true },
  idle: { dot: "bg-muted-foreground/40", label: "idle", ping: false },
  error: { dot: "bg-destructive", label: "error", ping: false },
};

function dotLabel(pane: WorkspacePaneRow, kind: AgentDotKind): string {
  if (kind === "working") return pane.activity === "starting" || pane.status === "pending" ? "starting" : "working";
  if (kind === "waiting") return "needs input";
  if (kind === "error") return pane.activity === "failed" ? "failed" : "error";
  if (pane.status === "exited") return "exited";
  return pane.worked ? "done" : "idle";
}

/**
 * Compact agents list for the active workspace tab.
 *
 * Sits under Workspaces in the sidebar while the Agentic IDE is on screen:
 * one row per coding agent of the workspace at the front — status dot, pane
 * name and agent — switching with the tab because it filters on the active
 * workspace id. Rows stay current through the shared pane store (poll plus
 * socket activity events), and a click asks the IDE view to bring that pane
 * forward via `requestPane`.
 */
export function IdeWorkspaceAgents() {
  const activeWorkspaceId = useIdeProjectsStore((state) => state.activeWorkspaceId);
  const requestPane = useIdeChatStore((state) => state.requestPane);
  const stagedPane = useIdeChatStore((state) => state.stagedPane);
  const panes = useWorkspacePanes();

  const mine = (activeWorkspaceId
    ? panes.filter(
        (pane) => pane.workspace_id === activeWorkspaceId && !pane.archived && pane.agent !== "shell",
      )
    : []
  ).slice().sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));

  return (
    <section
      data-testid="ide-workspace-agents"
      data-workspace={activeWorkspaceId ?? undefined}
      aria-label="Agents in this workspace"
      className="mx-2 mt-2 shrink-0 overflow-hidden rounded-2xl border border-border/50 bg-card/40 p-2 pb-3"
    >
      <div className="flex items-center justify-between px-2 pb-2 pt-1">
        <span className="text-[17px] font-semibold text-foreground">Agents</span>
        {mine.length > 0 && (
          <span
            data-testid="ide-workspace-agents-count"
            aria-label={`${mine.length} ${mine.length === 1 ? "agent" : "agents"}`}
            className="rounded-md bg-background/50 px-1.5 py-0.5 text-xs tabular-nums text-muted-foreground"
          >
            {mine.length}
          </span>
        )}
      </div>
      {activeWorkspaceId === null ? (
        <p className="px-2 py-1 text-xs text-muted-foreground">Select a workspace to see its agents.</p>
      ) : mine.length === 0 ? (
        <p className="px-2 py-1 text-xs text-muted-foreground">No agents running in this workspace.</p>
      ) : (
        <ul className="space-y-0.5">
          {mine.map((pane) => {
            const kind = dotKindFor(pane);
            const style = DOT_STYLE[kind];
            const stateLabel = dotLabel(pane, kind);
            const cli = pane.display_name || pane.agent;
            const selected = stagedPane !== null && pane.name === stagedPane;
            return (
              <li key={pane.history_id}>
                <button
                  type="button"
                  onClick={() => requestPane(pane.workspace_id, pane.name)}
                  title={`${pane.name} · ${cli} · ${stateLabel}`}
                  aria-label={`${pane.name}, ${cli}, ${stateLabel}`}
                  aria-current={selected ? "true" : undefined}
                  data-testid="ide-workspace-agent-row"
                  data-pane={pane.name}
                  data-kind={kind}
                  className={cn(
                    "flex min-h-10 w-full items-center gap-2.5 rounded-xl px-2.5 text-left transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring hover:bg-muted/70",
                    selected && "border border-border/50 bg-muted text-foreground",
                  )}
                >
                  <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
                    {style.ping && (
                      <span className="absolute inset-0 animate-ping rounded-full bg-current opacity-60 [animation-duration:1.8s] motion-reduce:hidden text-warning" />
                    )}
                    <span className={cn("relative h-2 w-2 rounded-full", style.dot)} />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[15px] font-medium text-foreground">
                    {pane.name}
                  </span>
                  <span className="max-w-[45%] shrink-0 truncate text-xs text-muted-foreground">{cli}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
