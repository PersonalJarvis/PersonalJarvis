/**
 * The fixed agents rail beside the stage (MASTERPLAN §4.1): the lead as a
 * compact centered master above the team — swatch and name in a small box —
 * then a search field and one row per remaining agent, plus the "+" that
 * opens the creator. Names open the chat; avatars open a compact profile
 * dialog without switching the conversation.
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
import { lazy, Suspense, useCallback, useMemo, useState, type DragEvent, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { Eye, GripVertical, Loader2, Plus, Search } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { AgentSwatch } from "../AgentSwatch";
import type { AgentRunState, SocietyAgent } from "../data";
import { AgentRosterActions } from "./AgentRosterActions";
import { useRosterUnread } from "./useRosterUnread";

const HIDDEN_AGENTS_KEY = "society.roster.hidden-agent-ids";
const ORDER_KEY = "society.roster.order";

function readHiddenAgents(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(HIDDEN_AGENTS_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

/**
 * The person's own row order, newest drag last. Unknown ids (retired agents,
 * a fresh install) are ignored when sorting; agents missing from the list
 * keep their default place at the end until the next drag persists them.
 */
function readRosterOrder(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(ORDER_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

const AgentProfileDialog = lazy(() => import("../card/AgentProfileDialog").then((module) => ({ default: module.AgentProfileDialog })));

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
  /** Sits above the title — the way back to the rest of the app. */
  header?: ReactNode;
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
  header,
}: RosterRailProps) {
  const t = useT();
  const [query, setQuery] = useState("");
  const [profileId, setProfileId] = useState<string | null>(null);
  const [hiddenIds, setHiddenIds] = useState(readHiddenAgents);
  const [orderIds, setOrderIds] = useState(readRosterOrder);
  const [dragId, setDragId] = useState<string | null>(null);
  const [dropId, setDropId] = useState<string | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const [menu, setMenu] = useState<{ agentId: string; x: number; y: number } | null>(null);
  const profile = agents.find((agent) => agent.agentId === profileId);
  const menuAgent = agents.find((agent) => agent.agentId === menu?.agentId);
  const unread = useRosterUnread(agents, activeAgentId);

  const setHidden = useCallback((agentId: string, hidden: boolean) => {
    setHiddenIds((current) => {
      const next = hidden ? [...new Set([...current, agentId])] : current.filter((id) => id !== agentId);
      localStorage.setItem(HIDDEN_AGENTS_KEY, JSON.stringify(next));
      return next;
    });
  }, []);
  const closeMenu = useCallback(() => setMenu(null), []);
  const openMenu = (event: MouseEvent, agentId: string) => {
    event.preventDefault();
    event.stopPropagation();
    setMenu({ agentId, x: event.clientX, y: event.clientY });
  };

  const lead = useMemo(() => agents.find((a) => a.tier === "lead") ?? null, [agents]);

  const { leadVisible, rows } = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = agents.filter((a) =>
      (showHidden || !hiddenIds.includes(a.agentId)) &&
      (!q || `${a.name} ${a.title}`.toLowerCase().includes(q)),
    );
    const masterVisible = lead ? filtered.some((a) => a.agentId === lead.agentId) : false;
    // Orchestrators, then specialists; stable within a tier. The lead lives
    // in its own centered hero above and never repeats in the list.
    const rank = { lead: 0, orchestrator: 1, specialist: 2 } as const;
    const rest = filtered
      .filter((a) => a.agentId !== lead?.agentId);
    // A drag persists the full visible order, so from then on the person's
    // own arrangement wins over the tier grouping. Agents the list never saw
    // (freshly created, or a retired id still stored) settle at the end in
    // tier order until the next drag files them in.
    const position = new Map(orderIds.map((id, index) => [id, index] as const));
    const custom = rest.some((a) => position.has(a.agentId));
    rest.sort((a, b) => {
      if (custom) {
        const ai = position.get(a.agentId);
        const bi = position.get(b.agentId);
        if (ai !== undefined && bi !== undefined) return ai - bi;
        if (ai !== undefined) return -1;
        if (bi !== undefined) return 1;
      }
      return rank[a.tier] - rank[b.tier];
    });
    return { leadVisible: masterVisible, rows: rest };
  }, [agents, hiddenIds, lead, orderIds, query, showHidden]);

  // While searching, the list is a filtered excerpt — dragging there would
  // file agents by where they happen to sit in the excerpt, so rows stay put.
  const reorderable = query.trim() === "";

  /** File `fromId` in directly before `toId`, keeping every other row where it is. */
  const moveBefore = useCallback((fromId: string, toId: string) => {
    if (fromId === toId) return;
    setOrderIds((current) => {
      const base = rows.map((a) => a.agentId);
      const known = new Set(base);
      // Carry over stored ids for rows currently filtered out (hidden while
      // "show hidden" is off) so a drag never silently drops them.
      for (const id of current) if (!known.has(id)) base.push(id);
      const without = base.filter((id) => id !== fromId);
      const at = without.indexOf(toId);
      const next = at === -1 ? [...without, fromId] : [...without.slice(0, at), fromId, ...without.slice(at)];
      try {
        localStorage.setItem(ORDER_KEY, JSON.stringify(next));
      } catch {
        // Private mode: the order holds for this window.
      }
      return next;
    });
  }, [rows]);

  /** Keyboard twin of the drag: Alt + Arrow Up / Down steps the row. */
  const moveStep = useCallback((agentId: string, delta: -1 | 1) => {
    setOrderIds((current) => {
      const base = rows.map((a) => a.agentId);
      const known = new Set(base);
      for (const id of current) if (!known.has(id)) base.push(id);
      const at = base.indexOf(agentId);
      const swap = at + delta;
      if (at === -1 || swap < 0 || swap >= base.length) return current;
      const next = [...base];
      [next[at], next[swap]] = [next[swap], next[at]];
      try {
        localStorage.setItem(ORDER_KEY, JSON.stringify(next));
      } catch {
        // Private mode: the order holds for this window.
      }
      return next;
    });
  }, [rows]);

  const onRowKeyDown = (event: KeyboardEvent, agentId: string) => {
    if (!reorderable || !event.altKey) return;
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      event.preventDefault();
      moveStep(agentId, event.key === "ArrowUp" ? -1 : 1);
    }
  };

  const onDragStart = (event: DragEvent, agentId: string) => {
    setDragId(agentId);
    try {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", agentId);
    } catch {
      // jsdom / touch: the state above still drives the drop.
    }
  };

  const onDragOverRow = (event: DragEvent, agentId: string) => {
    if (!dragId || dragId === agentId) return;
    event.preventDefault();
    try {
      event.dataTransfer.dropEffect = "move";
    } catch {
      // Non-HTML backends ignore the hint; the drop still lands.
    }
    if (dropId !== agentId) setDropId(agentId);
  };

  const onDropRow = (event: DragEvent, agentId: string) => {
    event.preventDefault();
    const from = dragId ?? (() => {
      try {
        return event.dataTransfer.getData("text/plain") || null;
      } catch {
        return null;
      }
    })();
    if (from && from !== agentId) moveBefore(from, agentId);
    setDragId(null);
    setDropId(null);
  };

  const endDrag = useCallback(() => {
    setDragId(null);
    setDropId(null);
  }, []);

  const hiddenCount = agents.filter((agent) => hiddenIds.includes(agent.agentId)).length;

  return (
    <aside
      data-testid="society-roster-rail"
      className={cn(
        "flex h-full min-h-0 flex-col border-border bg-sidebar",
        side === "left" ? "border-r border-border" : "border-l border-border",
        className ?? "w-[300px] shrink-0",
      )}
    >
      {header}
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
      {hiddenCount > 0 && <button type="button" onClick={() => setShowHidden((value) => !value)}
        className="mx-3 mt-2 flex items-center gap-1.5 rounded-md px-2 py-1 text-left text-xs text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <Eye className="h-3.5 w-3.5" aria-hidden />
        {t(showHidden ? "society.roster.hide_hidden" : "society.roster.show_hidden").replace("{0}", String(hiddenCount))}
      </button>}
      <ScrollArea className="mt-2 min-h-0 flex-1">
        {lead && leadVisible ? (
          <div className="flex justify-center px-2 pb-2">
            <div
              data-testid="society-lead-hero"
              onContextMenu={(event) => openMenu(event, lead.agentId)}
              className={cn(
                "flex flex-col items-center gap-1.5 rounded-xl bg-secondary/50 px-5 py-3 text-center transition-colors hover:bg-secondary/80",
                lead.agentId === activeAgentId && "bg-secondary",
              )}
            >
              <button type="button" onClick={() => setProfileId(lead.agentId)} aria-label={t("society.profile_card.open").replace("{0}", lead.name)} className="relative rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
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
                    className="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full bg-sky-400 ring-2 ring-sidebar"
                  />
                ) : null}
              </button>
              <button type="button" onClick={() => onOpen(lead.agentId)} aria-current={lead.agentId === activeAgentId ? "true" : undefined} className="flex max-w-full items-center justify-center gap-1.5 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <span className="truncate text-sm font-medium text-foreground">{lead.name}</span>
                <Badge variant="secondary" className="shrink-0 px-1.5 py-0 text-xs">
                  {t("society.tier.lead")}
                </Badge>
              </button>
            </div>
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
              <div
                data-agent-id={agent.agentId}
                draggable={reorderable}
                onDragStart={(event) => onDragStart(event, agent.agentId)}
                onDragOver={(event) => onDragOverRow(event, agent.agentId)}
                onDragLeave={() => setDropId((current) => (current === agent.agentId ? null : current))}
                onDrop={(event) => onDropRow(event, agent.agentId)}
                onDragEnd={endDrag}
                onKeyDown={(event) => onRowKeyDown(event, agent.agentId)}
                onContextMenu={(event) => openMenu(event, agent.agentId)}
                title={reorderable ? `${t("society.roster.reorder")} · ${t("society.roster.reorder_keys")}` : undefined}
                aria-keyshortcuts={reorderable ? "Alt+ArrowUp Alt+ArrowDown" : undefined}
                className={cn(
                  "group flex w-full items-center rounded-md px-2 text-left transition-colors hover:bg-secondary",
                  agent.agentId === activeAgentId && "bg-secondary",
                  hiddenIds.includes(agent.agentId) && "opacity-60",
                  dragId === agent.agentId && "opacity-40",
                  dropId === agent.agentId && "bg-secondary ring-1 ring-inset ring-border-strong",
                )}
              >
                <span
                  aria-hidden
                  className="-ml-1 shrink-0 cursor-grab text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
                >
                  <GripVertical className="h-4 w-4" />
                </span>
                <button type="button" onClick={() => setProfileId(agent.agentId)} aria-label={t("society.profile_card.open").replace("{0}", agent.name)} className="shrink-0 rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <AgentSwatch agent={agent} size={48} />
                </button>
                <button type="button" onClick={() => onOpen(agent.agentId)} aria-current={agent.agentId === activeAgentId ? "true" : undefined} className="flex min-w-0 flex-1 select-none items-center gap-2.5 rounded-md py-2 pl-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
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
              </div>
            </li>
          ))}
        </ul>
      </ScrollArea>
      {menu && menuAgent && <AgentRosterActions key={menu.agentId} agent={menuAgent} roster={agents} sample={sample}
        hidden={hiddenIds.includes(menu.agentId)} x={menu.x} y={menu.y} onVisibilityChange={setHidden} onDismiss={closeMenu} />}
      {profile && <Suspense fallback={null}><AgentProfileDialog key={profile.agentId} agent={profile} sample={sample} onClose={() => setProfileId(null)} /></Suspense>}
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
        className="h-2 w-2 shrink-0 rounded-full bg-sky-400"
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
