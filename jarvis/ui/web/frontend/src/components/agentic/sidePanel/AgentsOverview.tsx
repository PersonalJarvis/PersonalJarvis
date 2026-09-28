import { useEffect, useState } from "react";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { useWorkspacePanes } from "@/store/workspacePanes";
import { AgentMark } from "@/components/agentic/AgentMark";
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

/** How often the "3m" readings move on; state changes arrive live anyway. */
const CLOCK_TICK_MS = 15_000;

function useClock(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

/** Ink and a faint fill per state, from the semantic status tokens only. */
const STATE_TONE: Record<AgentDotKind, { text: string; pill: string }> = {
  waiting: { text: "text-warning", pill: "bg-warning/10" },
  working: { text: "text-success", pill: "bg-success/10" },
  error: { text: "text-destructive", pill: "bg-destructive/10" },
  idle: { text: "text-muted-foreground", pill: "bg-muted" },
};

/**
 * What every coding agent of the workspace at the front is doing right now.
 *
 * The side panel's Agents tab: a strip of state counts over one card per
 * agent. A card leads with the agent's brand mark — which CLI it is is the
 * first thing to read — then its call-sign, its state and for how long, what
 * it is working on, and when it last printed anything. Rows stay live through
 * the shared pane store; a click brings the pane forward and frames it in the
 * grid (the `spotlight`), so the card and its terminal are visibly one thing.
 */
export function AgentsOverview() {
  const t = useT();
  const now = useClock();
  const activeWorkspaceId = useIdeProjectsStore((state) => state.activeWorkspaceId);
  const requestPane = useIdeChatStore((state) => state.requestPane);
  const spotlight = useIdeSidePanelStore((state) => state.spotlight);
  const setSpotlight = useIdeSidePanelStore((state) => state.setSpotlight);
  const panes = useWorkspacePanes();
  const mine = workspaceAgents(panes, activeWorkspaceId);

  const counts = new Map<AgentDotKind, number>();
  for (const pane of mine) {
    const kind = dotKindFor(pane);
    counts.set(kind, (counts.get(kind) ?? 0) + 1);
  }

  const pick = (pane: WorkspacePaneRow) => {
    setSpotlight({ workspaceId: pane.workspace_id, pane: pane.name });
    requestPane(pane.workspace_id, pane.name);
  };

  return (
    <section
      data-testid="ide-workspace-agents"
      data-workspace={activeWorkspaceId ?? undefined}
      aria-label={t("ide_side_panel.agents.aria")}
      className="flex h-full min-h-0 flex-col"
    >
      {mine.length > 0 && (
        <div data-testid="ide-agents-summary" className="shrink-0 space-y-2 px-3 pb-2 pt-3">
          <div className="flex items-baseline justify-between px-1">
            <span data-testid="ide-workspace-agents-count" className="text-sm font-semibold text-foreground">
              {fill(t(mine.length === 1 ? "ide_side_panel.agents.count_one" : "ide_side_panel.agents.count_other"), { n: mine.length })}
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {SUMMARY_ORDER.filter((kind) => counts.get(kind)).map((kind) => (
              <span
                key={kind}
                data-kind={kind}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium tabular-nums",
                  STATE_TONE[kind].pill,
                  STATE_TONE[kind].text,
                )}
              >
                <span className={cn("h-1.5 w-1.5 rounded-full", DOT_STYLE[kind].dot)} aria-hidden="true" />
                {fill(t(`ide_side_panel.agents.summary.${kind}`), { n: counts.get(kind) ?? 0 })}
              </span>
            ))}
          </div>
        </div>
      )}

      {activeWorkspaceId === null ? (
        <p className="px-4 py-3 text-sm text-muted-foreground">{t("ide_side_panel.agents.no_workspace")}</p>
      ) : mine.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted-foreground">{t("ide_side_panel.agents.empty")}</p>
      ) : (
        <ul className="scrollbar-jarvis min-h-0 flex-1 space-y-2 overflow-y-auto px-3 pb-3 pt-1">
          {mine.map((pane) => {
            const kind = dotKindFor(pane);
            const style = DOT_STYLE[kind];
            const tone = STATE_TONE[kind];
            const stateLabel = t(`ide_side_panel.agents.state.${stateKeyFor(pane, kind)}`);
            const cli = pane.display_name || pane.agent;
            const selected =
              spotlight !== null && spotlight.workspaceId === pane.workspace_id && spotlight.pane === pane.name;
            const since = compactSince(pane.activity_since, now);
            const lastOutput = compactSince(pane.last_output_at, now);
            const title = sessionTitle(pane);
            const hasTitle = title !== cli && title !== pane.name;
            return (
              <li key={pane.history_id}>
                <button
                  type="button"
                  onClick={() => pick(pane)}
                  aria-label={`${pane.name}, ${cli}, ${stateLabel}`}
                  aria-current={selected ? "true" : undefined}
                  data-testid="ide-workspace-agent-row"
                  data-pane={pane.name}
                  data-kind={kind}
                  className={cn(
                    "group flex w-full flex-col gap-2.5 rounded-xl border p-3 text-left transition-[border-color,background-color,box-shadow] duration-150",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none",
                    selected
                      ? "border-accent bg-accent/[0.06] ring-1 ring-accent/40"
                      : "border-border/60 bg-background/40 hover:border-border hover:bg-muted/50",
                  )}
                >
                  <span className="flex w-full items-center gap-3">
                    <span className="relative shrink-0">
                      <AgentMark agent={pane.agent} label={cli} size="md" />
                      <span
                        aria-hidden="true"
                        className="absolute -bottom-0.5 -right-0.5 flex h-3 w-3 items-center justify-center rounded-full bg-card"
                      >
                        {style.ping && (
                          <span className="absolute inset-0.5 animate-ping rounded-full bg-warning opacity-60 [animation-duration:1.8s] motion-reduce:hidden" />
                        )}
                        <span className={cn("relative h-2 w-2 rounded-full", style.dot)} />
                      </span>
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="flex min-w-0 items-baseline gap-2">
                        <span className="shrink-0 text-sm font-semibold text-foreground">{pane.name}</span>
                        <span className="min-w-0 truncate text-xs text-muted-foreground">{cli}</span>
                      </span>
                      {lastOutput && (
                        <span className="text-[11px] tabular-nums text-muted-foreground">
                          {fill(t("ide_side_panel.agents.last_output"), { time: lastOutput })}
                        </span>
                      )}
                    </span>
                    <span
                      data-testid="ide-agent-state"
                      className={cn(
                        "shrink-0 rounded-md px-2 py-0.5 text-[11px] font-medium tabular-nums",
                        tone.pill,
                        tone.text,
                      )}
                    >
                      {stateLabel}
                      {since && <span className="opacity-70"> · {since}</span>}
                    </span>
                  </span>
                  {hasTitle && (
                    <span
                      data-testid="ide-agent-task"
                      className="line-clamp-2 w-full border-t border-border/50 pt-2 text-[13px] leading-snug text-foreground/85"
                    >
                      {title}
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
