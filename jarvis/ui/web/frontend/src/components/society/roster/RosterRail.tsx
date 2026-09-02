/**
 * The fixed agents rail beside the stage (MASTERPLAN §4.1): one row per
 * agent — swatch, name, title, state dot — a search field and the "+" that
 * opens the creator. One click on a row opens the model card (the row-click
 * doctrine: open and activate in one click, never a double-click split).
 *
 * The rail is app chrome: Ink & Paper tokens, both modes. The world beside
 * it carries its own branding; nothing here leaks into the viewport.
 */
import { useMemo, useState } from "react";
import { Plus, Search } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { AgentSwatch } from "../AgentSwatch";
import type { AgentRunState, SocietyAgent } from "../data";

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
}

export function RosterRail({ agents, loading, sample, activeAgentId, onOpen, onCreate }: RosterRailProps) {
  const t = useT();
  const [query, setQuery] = useState("");

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q
      ? agents.filter((a) => `${a.name} ${a.title}`.toLowerCase().includes(q))
      : agents;
    // Lead first, then orchestrators, then specialists; stable within a tier.
    const rank = { lead: 0, orchestrator: 1, specialist: 2 } as const;
    return [...list].sort((a, b) => rank[a.tier] - rank[b.tier]);
  }, [agents, query]);

  return (
    <aside
      data-testid="society-roster-rail"
      className="flex h-full w-[300px] shrink-0 flex-col border-l border-border bg-sidebar"
    >
      <div className="flex items-center justify-between gap-2 px-3 pt-3">
        <div className="flex min-w-0 items-center gap-2">
          <h2 className="font-display text-sm font-semibold tracking-tight text-foreground">
            {t("society.roster.title")}
          </h2>
          {sample ? (
            <Badge variant="outline" className="text-[10px]">
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
          className="h-8 w-full rounded-md border border-border bg-background pl-8 pr-2 text-[13px] text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong"
        />
      </label>
      <ScrollArea className="mt-2 min-h-0 flex-1">
        <ul className="flex flex-col gap-0.5 px-2 pb-3">
          {loading && rows.length === 0 ? (
            <li className="px-2 py-3 text-xs text-muted-foreground">{t("society.roster.loading")}</li>
          ) : null}
          {!loading && rows.length === 0 ? (
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
                    <span className="truncate text-[13px] font-medium text-foreground">{agent.name}</span>
                    {agent.tier === "lead" ? (
                      <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">
                        {t("society.tier.lead")}
                      </Badge>
                    ) : null}
                  </span>
                  <span className="block truncate text-[11px] text-muted-foreground">{agent.title}</span>
                </span>
                <span
                  className={cn("h-2 w-2 shrink-0 rounded-full", STATE_DOT[agent.state])}
                  title={t(`society.state.${agent.state}`)}
                  aria-label={t(`society.state.${agent.state}`)}
                />
              </button>
            </li>
          ))}
        </ul>
      </ScrollArea>
    </aside>
  );
}
