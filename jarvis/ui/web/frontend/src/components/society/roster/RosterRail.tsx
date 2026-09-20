/**
 * The fixed agents rail beside the stage (MASTERPLAN §4.1): the lead as a
 * compact centered master above the team — swatch and name in a small box —
 * then a search field and one row per remaining agent, plus the "+" that
 * opens the creator. One click on the master or a row opens the model card
 * (the row-click doctrine: open and activate in one click, never a
 * double-click split).
 *
 * The layout follows the Chef Bot reference: the master is centered and
 * larger, the team stays a compact list below the search. The rail is app
 * chrome: Ink & Paper tokens, both modes. The world beside it carries its
 * own branding; nothing here leaks into the viewport.
 *
 * It sits on either edge. Beside the island it is the RIGHT rail with its own
 * fixed width; inside the agent card it is the LEFT eighth and takes its width
 * from the grid cell — same rows, same sizes, only the divider swaps sides.
 */
import { useMemo, useState } from "react";
import { Loader2, Plus, Search } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { AgentSwatch } from "../AgentSwatch";
import type { AgentRunState, SocietyAgent } from "../data";
import { useRosterUnread } from "./useRosterUnread";

const STATE_DOT: Record<AgentRunState, string> = {
  idle: "bg-muted-foreground/50",
  working: "bg-success",
  waiting: "bg-warning",
  paused: "bg-muted-foreground/30",
};

export interface RosterRailProps {
  agents: SocietyAgent[];
  loading: boolean;
  /** True while rows come from the sample roster rather than society.db. */
  sample: boolean;
  activeAgentId: string | null;
  onOpen: (agentId: string) => void;
  onCreate: () => void;
  /** Which edge the rail sits on; decides which side carries the divider. */
  side?: "left" | "right";
  /** Replaces the fixed width when the rail is a grid cell rather than a flex sibling. */
  className?: string;
}

export function RosterRail({
  agents,
  loading,
  sample,
  activeAgentId,
  onOpen,
  onCreate,
  side = "right",
  className,
}: RosterRailProps) {
  const t = useT();
  const [query, setQuery] = useState("");
  const unread = useRosterUnread(agents, activeAgentId);

  const lead = useMemo(() => agents.find((a) => a.tier === "lead") ?? null, [agents]);

  const { leadVisible, rows } = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? agents.filter((a) => `${a.name} ${a.title}`.toLowerCase().includes(q))
      : agents;
    const masterVisible = lead ? filtered.some((a) => a.agentId === lead.agentId) : false;
    // Orchestrators, then specialists; stable within a tier. The lead lives
    // in its own centered hero above and never repeats in the list.
    const rank = { lead: 0, orchestrator: 1, specialist: 2 } as const;
    const rest = filtered
      .filter((a) => a.agentId !== lead?.agentId)
      .sort((a, b) => rank[a.tier] - rank[b.tier]);
    return { leadVisible: masterVisible, rows: rest };
  }, [agents, lead, query]);

  return (
    <aside
      data-testid="society-roster-rail"
      className={cn(
        "flex h-full min-h-0 flex-col border-border bg-sidebar",
        side === "left" ? "border-r" : "border-l",
        className ?? "w-[300px] shrink-0",
      )}
    >
      <div className="flex items-center justify-between gap-2 px-3 pt-3">
        <div className="flex min-w-0 items-center gap-2">
          <h2 className="font-display text-sm font-semibold tracking-tight text-foreground">
            {t("society.roster.title")}
          </h2>
          {sample ? (
            <Badge variant="outline" className="text-xs">
              {t("society.sample_badge")}
            </Badge>
          ) : null}
        </div>
        <Button
          size="sm"
          variant="secondary"
          className="h-8 gap-1 px-2.5"
          onClick={onCreate}
          data-testid="society-create-button"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
          {t("society.roster.create")}
        </Button>
      </div>
      <label className="relative mx-3 mt-3 block">
        <Search
          className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("society.roster.search")}
          aria-label={t("society.roster.search")}
          className="h-8 w-full rounded-md border border-border bg-background pl-8 pr-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong"
        />
      </label>
      <ScrollArea className="mt-2 min-h-0 flex-1">
        {lead && leadVisible ? (
          <div className="flex justify-center px-2 pb-2">
            <button
              type="button"
              onClick={() => onOpen(lead.agentId)}
              aria-current={lead.agentId === activeAgentId ? "true" : undefined}
              data-testid="society-lead-hero"
              className={cn(
                "flex flex-col items-center gap-1.5 rounded-xl bg-secondary/50 px-5 py-3 text-center transition-colors hover:bg-secondary/80",
                lead.agentId === activeAgentId && "bg-secondary",
              )}
            >
              <span className="relative">
                <AgentSwatch agent={lead} size={56} />
                {lead.state === "working" ? (
                  <span
                    role="status"
                    aria-label={t("society.roster.thinking")}
                    title={t("society.roster.thinking")}
                    className="absolute -right-1 -top-1 grid h-5 w-5 place-items-center rounded-full bg-sidebar ring-2 ring-sidebar"
                  >
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />
                  </span>
                ) : unread.has(lead.agentId) ? (
                  <span
                    aria-label={t("society.roster.unread")}
                    title={t("society.roster.unread")}
                    className="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full bg-success ring-2 ring-sidebar"
                  />
                ) : null}
              </span>
              <span className="flex max-w-full items-center justify-center gap-1.5">
                <span className="truncate text-sm font-medium text-foreground">{lead.name}</span>
                <Badge variant="secondary" className="shrink-0 px-1.5 py-0 text-xs">
                  {t("society.tier.lead")}
                </Badge>
              </span>
            </button>
          </div>
        ) : null}
        {leadVisible ? (
          <div className="mx-3 mb-1 border-t border-border/60" aria-hidden />
        ) : null}
        <ul className="flex flex-col gap-0.5 px-2 pb-3">
          {loading && !leadVisible && rows.length === 0 ? (
            <li className="px-2 py-3 text-xs text-muted-foreground">{t("society.roster.loading")}</li>
          ) : null}
          {!loading && !leadVisible && rows.length === 0 ? (
            <li className="px-2 py-3 text-xs text-muted-foreground">{t("society.roster.empty")}</li>
          ) : null}
          {rows.map((agent) => (
            <li key={agent.agentId}>
              <button
                type="button"
                onClick={() => onOpen(agent.agentId)}
                aria-current={agent.agentId === activeAgentId ? "true" : undefined}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left transition-colors hover:bg-secondary",
                  agent.agentId === activeAgentId && "bg-secondary",
                )}
              >
                <AgentSwatch agent={agent} size={34} />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span className="truncate text-sm font-medium text-foreground">{agent.name}</span>
                    {agent.tier === "lead" ? (
                      <Badge variant="secondary" className="px-1.5 py-0 text-xs">
                        {t("society.tier.lead")}
                      </Badge>
                    ) : null}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">{agent.title}</span>
                </span>
                <RowStatus agent={agent} hasUnread={unread.has(agent.agentId)} />
              </button>
            </li>
          ))}
        </ul>
      </ScrollArea>
    </aside>
  );
}

/**
 * The three states the roster dot can be in: thinking (a loading spinner
 * while `state` is working), fresh results (green until the row is opened),
 * or the plain backend state (grey idle, amber waiting, faint paused).
 */
function RowStatus({ agent, hasUnread }: { agent: SocietyAgent; hasUnread: boolean }) {
  const t = useT();
  if (agent.state === "working") {
    const label = t("society.roster.thinking");
    return (
      <span
        role="status"
        aria-label={label}
        title={label}
        className="grid h-4 w-4 shrink-0 place-items-center"
      >
        <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" aria-hidden />
      </span>
    );
  }
  if (hasUnread) {
    const label = t("society.roster.unread");
    return (
      <span
        className="h-2 w-2 shrink-0 rounded-full bg-success"
        title={label}
        aria-label={label}
      />
    );
  }
  return (
    <span
      className={cn("h-2 w-2 shrink-0 rounded-full", STATE_DOT[agent.state])}
      title={t(`society.state.${agent.state}`)}
      aria-label={t(`society.state.${agent.state}`)}
    />
  );
}
