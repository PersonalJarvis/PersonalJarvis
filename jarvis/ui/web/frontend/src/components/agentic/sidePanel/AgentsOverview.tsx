import { useEffect, useState } from "react";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { stopIdeRuntime, type WorkspacePaneRow } from "@/lib/agenticIdeApi";
import { useEventStore } from "@/store/events";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { useWorkspacePanes } from "@/store/workspacePanes";
import { usePaneReviewsStore } from "@/store/paneReviews";
import { AgentMark } from "@/components/agentic/AgentMark";
import { paneTitleFrom, usePaneRecapPoll, usePaneRecapsStore } from "@/store/paneRecaps";
import { CheckCheck, Eye, Loader2, Search, X, type LucideIcon } from "lucide-react";
import { agentSearchDocument } from "./agentSearch";
import { useAgentSearch } from "./useAgentSearch";
import {
  COLUMN_ORDER,
  DOT_STYLE,
  columnFor,
  compactSince,
  dotKindFor,
  folderAgents,
  stateKeyFor,
  workspaceAgents,
  type AgentColumn,
  type AgentDotKind,
  type AgentScope,
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

const SCOPE_KEY = "jarvis.agenticIde.agentsScope";
const SCOPES: readonly AgentScope[] = ["workspace", "folder"];

function storedScope(): AgentScope {
  try {
    return localStorage.getItem(SCOPE_KEY) === "folder" ? "folder" : "workspace";
  } catch {
    return "workspace";
  }
}

/** The tab's own filter, remembered per browser: this workspace, or its whole folder. */
function useAgentScope(): [AgentScope, (scope: AgentScope) => void] {
  const [scope, setScope] = useState<AgentScope>(storedScope);
  const choose = (next: AgentScope) => {
    setScope(next);
    try {
      localStorage.setItem(SCOPE_KEY, next);
    } catch {
      /* a convenience only: the filter still works for this session */
    }
  };
  return [scope, choose];
}

/** Ink and a faint fill per state, from the semantic status tokens only. */
const STATE_TONE: Record<AgentDotKind, { text: string; pill: string }> = {
  waiting: { text: "text-warning", pill: "bg-warning/10" },
  working: { text: "text-accent", pill: "bg-accent/10" },
  error: { text: "text-destructive", pill: "bg-destructive/10" },
  idle: { text: "text-muted-foreground", pill: "bg-muted" },
};

const COLUMN_ICON: Record<AgentColumn, LucideIcon> = {
  done: CheckCheck,
  working: Loader2,
  reviewed: Eye,
};
const COLUMN_TONE: Record<AgentColumn, string> = {
  done: "text-success",
  working: "text-accent",
  reviewed: "text-muted-foreground",
};

/**
 * What every coding agent of the workspace at the front is doing right now.
 *
 * The side panel's Agents tab, in three columns top to bottom: Done (finished
 * and not looked at yet — what needs the user), Working, and Reviewed (a
 * finished agent whose pane the user has clicked since; see
 * `store/paneReviews`). A card leads with the agent's brand mark, then its
 * goal, its state and for how long, and when it last printed anything. A click
 * brings the pane forward, frames it in the grid (the `spotlight`) and counts
 * as reviewing it. A switch at the top widens the list from the workspace at
 * the front to every workspace of its project folder; a card from another
 * workspace names it, and its click brings that workspace forward.
 */
export function AgentsOverview() {
  const t = useT();
  const now = useClock();
  const activeWorkspaceId = useIdeProjectsStore((state) => state.activeWorkspaceId);
  const requestPane = useIdeChatStore((state) => state.requestPane);
  const spotlight = useIdeSidePanelStore((state) => state.spotlight);
  const setSpotlight = useIdeSidePanelStore((state) => state.setSpotlight);
  const panes = useWorkspacePanes();
  usePaneRecapPoll();
  const recaps = usePaneRecapsStore((state) => state);
  const [scope, setScope] = useAgentScope();
  const activeFolder = useIdeProjectsStore(
    (state) =>
      state.projects.flatMap((project) => project.workspaces).find((workspace) => workspace.id === activeWorkspaceId)
        ?.folder ?? null,
  );
  const mine =
    scope === "folder"
      ? folderAgents(panes, activeWorkspaceId, activeFolder)
      : workspaceAgents(panes, activeWorkspaceId);
  const reviewed = usePaneReviewsStore((state) => state.reviewed);
  const markReviewed = usePaneReviewsStore((state) => state.markReviewed);
  const pushToast = useEventStore((state) => state.pushToast);
  const requestRefresh = useIdeProjectsStore((state) => state.requestRefresh);
  const [confirmStop, setConfirmStop] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [query, setQuery] = useState("");
  const searching = query.trim().length > 0;
  const search = useAgentSearch(query, mine.map((pane) => agentSearchDocument(
    pane,
    paneTitleFrom(recaps.workspaceId === pane.workspace_id ? recaps.byName[pane.name] : undefined, pane),
  )));
  const byId = new Map(mine.map((pane) => [pane.history_id, pane]));
  const matches = search.matches.flatMap(({ id }) => {
    const pane = byId.get(id);
    return pane ? [pane] : [];
  });

  const columns: Record<AgentColumn, WorkspacePaneRow[]> = { done: [], working: [], reviewed: [] };
  for (const pane of mine) columns[columnFor(pane, reviewed[pane.history_id])].push(pane);
  // Newest news first in Done and Reviewed; the longest-running job first in Working.
  const settledAt = (pane: WorkspacePaneRow) => pane.activity_since || 0;
  columns.done.sort((a, b) => settledAt(b) - settledAt(a));
  columns.reviewed.sort((a, b) => settledAt(b) - settledAt(a));
  columns.working.sort((a, b) => settledAt(a) - settledAt(b));

  const pick = (pane: WorkspacePaneRow) => {
    markReviewed(pane.history_id);
    setSpotlight({ workspaceId: pane.workspace_id, pane: pane.name });
    requestPane(pane.workspace_id, pane.name);
  };

  const card = (pane: WorkspacePaneRow) => {
    const kind = dotKindFor(pane);
    const style = DOT_STYLE[kind];
    const tone = STATE_TONE[kind];
    const stateLabel = t(`ide_side_panel.agents.state.${stateKeyFor(pane, kind)}`);
    const cli = pane.display_name || pane.agent;
    const selected =
      spotlight !== null && spotlight.workspaceId === pane.workspace_id && spotlight.pane === pane.name;
    const stateSince = compactSince(pane.activity_since, now);
    const lastOutput = compactSince(pane.last_output_at, now);
    const recap = recaps.workspaceId === pane.workspace_id ? recaps.byName[pane.name] : undefined;
    const title = paneTitleFrom(recap, pane);
    const elsewhere = pane.workspace_id !== activeWorkspaceId ? pane.workspace_name : "";
    return (
      <li key={pane.history_id}>
        <button
          type="button"
          onClick={() => pick(pane)}
          aria-label={`${title || pane.name}, ${cli}, ${stateLabel}`}
          title={title ? `${title} (${pane.name})` : pane.name}
          aria-current={selected ? "true" : undefined}
          data-testid="ide-workspace-agent-row"
          data-pane={pane.name}
          data-workspace={pane.workspace_id}
          data-kind={kind}
          className={cn(
            "group flex w-full items-center rounded-xl border p-3 text-left transition-[border-color,background-color,box-shadow] duration-150",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none",
            selected
              ? "border-accent bg-accent/[0.06] ring-1 ring-accent/40"
              : "border-border/60 bg-background/40 hover:border-border hover:bg-muted/50",
          )}
        >
          <span className="flex w-full items-start gap-3">
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
            <span className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span
                data-testid="ide-agent-title"
                className="line-clamp-2 text-sm font-semibold leading-snug text-foreground"
              >
                {title || cli}
              </span>
              <span className="truncate text-[11px] tabular-nums text-muted-foreground">
                {[elsewhere, title ? cli : "", lastOutput ? fill(t("ide_side_panel.agents.last_output"), { time: lastOutput }) : ""]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
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
              {stateSince && <span className="opacity-70"> · {stateSince}</span>}
            </span>
          </span>
        </button>
      </li>
    );
  };

  // Closing the app only detaches from the agents (they live in a background
  // host), so ending them is a separate, deliberate action with its own confirm.
  const stopAll = async () => {
    setStopping(true);
    try {
      await stopIdeRuntime();
      requestRefresh();
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      setStopping(false);
      setConfirmStop(false);
    }
  };

  return (
    <section
      data-testid="ide-workspace-agents"
      data-workspace={activeWorkspaceId ?? undefined}
      aria-label={t("ide_side_panel.agents.aria")}
      className="flex h-full min-h-0 flex-col"
    >
      {activeWorkspaceId !== null && (
        <div
          role="radiogroup"
          aria-label={t("ide_side_panel.agents.scope.aria")}
          data-testid="ide-agents-scope"
          className="mx-3 mt-3 flex shrink-0 rounded-lg border border-border/60 bg-muted/40 p-0.5"
        >
          {SCOPES.map((option) => (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={scope === option}
              onClick={() => setScope(option)}
              data-testid={`ide-agents-scope-${option}`}
              className={cn(
                "flex-1 rounded-md px-2 py-1 text-xs font-medium transition-colors duration-150 motion-reduce:transition-none",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                scope === option
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t(`ide_side_panel.agents.scope.${option}`)}
            </button>
          ))}
        </div>
      )}
      {activeWorkspaceId !== null && (
        <div role="search" className="mx-3 mt-3 shrink-0 space-y-1.5">
          <div className="flex items-center gap-2 rounded-lg border border-border bg-background px-3 focus-within:ring-2 focus-within:ring-ring">
            <Search aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />
            <input
              type="search"
              value={query}
              maxLength={500}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape" && query) {
                  event.preventDefault();
                  event.stopPropagation();
                  setQuery("");
                }
              }}
              aria-label={t("ide_side_panel.agents.search.label")}
              aria-describedby="ide-agents-search-hint"
              placeholder={t("ide_side_panel.agents.search.placeholder")}
              className="h-9 min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground [&::-webkit-search-cancel-button]:hidden"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label={t("ide_side_panel.agents.search.clear")}
                className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X aria-hidden className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
          <p id="ide-agents-search-hint" className="px-1 text-[11px] text-muted-foreground">
            {t("ide_side_panel.agents.search.hint")}
          </p>
        </div>
      )}
      {activeWorkspaceId === null ? (
        <p className="px-4 py-3 text-sm text-muted-foreground">{t("ide_side_panel.agents.no_workspace")}</p>
      ) : searching ? (
        <div className="scrollbar-jarvis min-h-0 flex-1 overflow-y-auto px-3 pb-3 pt-3" aria-busy={search.status === "loading" || search.status === "searching"}>
          <p role="status" className="mb-2 flex items-center gap-2 px-1 text-xs text-muted-foreground">
            {(search.status === "loading" || search.status === "searching") && <Loader2 aria-hidden className="h-3.5 w-3.5 shrink-0 animate-spin motion-reduce:animate-none" />}
            {search.status === "loading" ? t("ide_side_panel.agents.search.loading")
              : search.status === "searching" ? t("ide_side_panel.agents.search.searching")
              : search.status === "error" ? t("ide_side_panel.agents.search.error")
              : matches.length === 0 ? t("ide_side_panel.agents.search.empty")
              : fill(t("ide_side_panel.agents.search.results"), { count: String(matches.length) })}
          </p>
          {search.status === "error" && (
            <button type="button" onClick={search.retry} className="rounded-md border border-border px-3 py-1.5 text-xs text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {t("ide_side_panel.agents.search.retry")}
            </button>
          )}
          {matches.length > 0 && <ul className="space-y-2">{matches.map(card)}</ul>}
        </div>
      ) : mine.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted-foreground">
          {t(scope === "folder" ? "ide_side_panel.agents.empty_folder" : "ide_side_panel.agents.empty")}
        </p>
      ) : (
        <div className="scrollbar-jarvis min-h-0 flex-1 space-y-4 overflow-y-auto px-3 pb-3 pt-3">
          {COLUMN_ORDER.map((column) => {
            const Icon = COLUMN_ICON[column];
            const rows = columns[column];
            return (
              <section
                key={column}
                data-testid={`ide-agents-column-${column}`}
                aria-label={t(`ide_side_panel.agents.columns.${column}`)}
              >
                <h3 className="mb-1.5 flex items-center gap-2 px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  <Icon
                    aria-hidden
                    className={cn(
                      "h-3.5 w-3.5",
                      COLUMN_TONE[column],
                      column === "working" && rows.length > 0 && "animate-spin [animation-duration:2.4s] motion-reduce:animate-none",
                    )}
                  />
                  <span>{t(`ide_side_panel.agents.columns.${column}`)}</span>
                  <span
                    data-testid={`ide-agents-column-count-${column}`}
                    className="rounded bg-muted px-1.5 text-[10px] tabular-nums text-muted-foreground"
                  >
                    {rows.length}
                  </span>
                </h3>
                {rows.length === 0 ? (
                  <p className="rounded-lg border border-dashed border-border/60 px-3 py-2 text-[11px] text-muted-foreground">
                    {t(`ide_side_panel.agents.column_empty.${column}`)}
                  </p>
                ) : (
                  <ul className="space-y-2">{rows.map(card)}</ul>
                )}
              </section>
            );
          })}
        </div>
      )}

      {panes.length > 0 && (
        <div data-testid="ide-agents-runtime" className="shrink-0 space-y-2 border-t border-border/60 px-4 py-3">
          <p className="text-[11px] text-muted-foreground">{t("ide_side_panel.agents.runtime_note")}</p>
          {confirmStop ? (
            <div role="alertdialog" aria-label={t("ide_side_panel.agents.stop_all")} className="space-y-2">
              <p className="text-xs text-foreground">{t("ide_side_panel.agents.stop_confirm")}</p>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={stopping}
                  onClick={() => void stopAll()}
                  data-testid="ide-agents-stop-confirm"
                  className="rounded-md bg-destructive px-2.5 py-1 text-xs font-medium text-destructive-foreground hover:bg-destructive/90 disabled:opacity-60"
                >
                  {t("ide_side_panel.agents.stop_confirm_yes")}
                </button>
                <button
                  type="button"
                  disabled={stopping}
                  onClick={() => setConfirmStop(false)}
                  className="rounded-md border border-border px-2.5 py-1 text-xs text-foreground hover:bg-muted"
                >
                  {t("ide_side_panel.agents.stop_cancel")}
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmStop(true)}
              data-testid="ide-agents-stop-all"
              className="rounded-md border border-border px-2.5 py-1 text-xs text-destructive hover:bg-destructive/10"
            >
              {t("ide_side_panel.agents.stop_all")}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
