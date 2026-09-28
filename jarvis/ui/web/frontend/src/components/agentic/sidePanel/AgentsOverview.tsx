import { useEffect, useState } from "react";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useWorkspacePanes } from "@/store/workspacePanes";
import { sessionTitle } from "@/components/agentic/sessionTitle";
import {
  DOT_STYLE,
  SUMMARY_ORDER,
  compactSince,
  dotKindFor,
  stateKeyFor,
  workspaceAgents,
  type AgentDotKind,
} from "./agentStatus";

/** How often the "for 3m" readings move on; state changes arrive live anyway. */
const CLOCK_TICK_MS = 15_000;

function useClock(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

const SUMMARY_TONE: Record<AgentDotKind, string> = {
  waiting: "text-warning",
  working: "text-success",
  error: "text-destructive",
  idle: "text-muted-foreground",
};

/**
 * What every coding agent of the workspace at the front is doing right now.
 *
 * The side panel's Agents tab: a summary strip (how many work, wait on the
 * user, failed, sit idle) over one card per agent — its state and for how
 * long, the CLI behind it, what it was last asked, and when it last printed
 * anything. Rows stay live through the shared pane store (poll plus socket
 * activity events); a click asks the IDE view to bring that pane forward.
 */
export function AgentsOverview() {
  const t = useT();
  const now = useClock();
  const activeWorkspaceId = useIdeProjectsStore((state) => state.activeWorkspaceId);
  const requestPane = useIdeChatStore((state) => state.requestPane);
  const stagedPane = useIdeChatStore((state) => state.stagedPane);
  const panes = useWorkspacePanes();
  const mine = workspaceAgents(panes, activeWorkspaceId);

  const counts = new Map<AgentDotKind, number>();
  for (const pane of mine) {
    const kind = dotKindFor(pane);
    counts.set(kind, (counts.get(kind) ?? 0) + 1);
  }

  return (
    <section
      data-testid="ide-workspace-agents"
      data-workspace={activeWorkspaceId ?? undefined}
      aria-label={t("ide_side_panel.agents.aria")}
      className="flex h-full min-h-0 flex-col"
    >
      {mine.length > 0 && (
        <div
          data-testid="ide-agents-summary"
          className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-border/60 px-4 py-2.5 text-xs"
        >
          <span
            data-testid="ide-workspace-agents-count"
            className="font-medium tabular-nums text-foreground"
          >
            {fill(t(mine.length === 1 ? "ide_side_panel.agents.count_one" : "ide_side_panel.agents.count_other"), { n: mine.length })}
          </span>
          {SUMMARY_ORDER.filter((kind) => counts.get(kind)).map((kind) => (
            <span key={kind} data-kind={kind} className={cn("flex items-center gap-1.5 tabular-nums", SUMMARY_TONE[kind])}>
              <span className={cn("h-1.5 w-1.5 rounded-full", DOT_STYLE[kind].dot)} aria-hidden="true" />
              {fill(t(`ide_side_panel.agents.summary.${kind}`), { n: counts.get(kind) ?? 0 })}
            </span>
          ))}
        </div>
      )}

      {activeWorkspaceId === null ? (
        <p className="px-4 py-3 text-sm text-muted-foreground">{t("ide_side_panel.agents.no_workspace")}</p>
      ) : mine.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted-foreground">{t("ide_side_panel.agents.empty")}</p>
      ) : (
        <ul className="scrollbar-jarvis min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
          {mine.map((pane) => {
            const kind = dotKindFor(pane);
            const style = DOT_STYLE[kind];
            const stateLabel = t(`ide_side_panel.agents.state.${stateKeyFor(pane, kind)}`);
            const cli = pane.display_name || pane.agent;
            const selected = stagedPane !== null && pane.name === stagedPane;
            const since = compactSince(pane.activity_since, now);
            const lastOutput = compactSince(pane.last_output_at, now);
            const title = sessionTitle(pane);
            const hasTitle = title !== cli && title !== pane.name;
            return (
              <li key={pane.history_id}>
                <button
                  type="button"
                  onClick={() => requestPane(pane.workspace_id, pane.name)}
                  aria-label={`${pane.name}, ${cli}, ${stateLabel}`}
                  aria-current={selected ? "true" : undefined}
                  data-testid="ide-workspace-agent-row"
                  data-pane={pane.name}
                  data-kind={kind}
                  className={cn(
                    "flex w-full flex-col gap-1 rounded-xl border border-transparent px-3 py-2.5 text-left transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring hover:bg-muted/70",
                    selected && "border-border/60 bg-muted",
                  )}
                >
                  <span className="flex w-full items-center gap-2.5">
                    <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
                      {style.ping && (
                        <span className="absolute inset-0 animate-ping rounded-full bg-warning opacity-60 [animation-duration:1.8s] motion-reduce:hidden" />
                      )}
                      <span className={cn("relative h-2 w-2 rounded-full", style.dot)} />
                    </span>
                    <span className="min-w-0 truncate text-[15px] font-medium text-foreground">{pane.name}</span>
                    <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{cli}</span>
                  </span>
                  <span className="flex w-full items-center gap-1.5 pl-[18px] text-xs">
                    <span data-testid="ide-agent-state" className={cn("font-medium", SUMMARY_TONE[kind])}>{stateLabel}</span>
                    {since && (
                      <span className="tabular-nums text-muted-foreground">
                        · {fill(t("ide_side_panel.agents.for"), { time: since })}
                      </span>
                    )}
                  </span>
                  {hasTitle && (
                    <span data-testid="ide-agent-task" className="line-clamp-2 pl-[18px] text-xs text-foreground/80">
                      {title}
                    </span>
                  )}
                  {lastOutput && (
                    <span className="pl-[18px] text-[11px] tabular-nums text-muted-foreground">
                      {fill(t("ide_side_panel.agents.last_output"), { time: lastOutput })}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
