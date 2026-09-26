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
import { lazy, Suspense, useCallback, useMemo, useState, type MouseEvent, type ReactNode } from "react";
import { Eye, Loader2, Plus, Search, UsersRound } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { SocietyChatGroup } from "@/lib/societyChatGroups";

import { AgentSwatch } from "../AgentSwatch";
import type { AgentRunState, SocietyAgent } from "../data";
import { AgentRosterActions } from "./AgentRosterActions";
import { useRosterUnread } from "./useRosterUnread";
import { ChatGroupDialog } from "../chat/ChatGroupDialog";

const HIDDEN_AGENTS_KEY = "society.roster.hidden-agent-ids";

function readHiddenAgents(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(HIDDEN_AGENTS_KEY) ?? "[]");
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
  groups?: SocietyChatGroup[];
  activeGroupId?: string | null;
  onOpenGroup?: (groupId: string) => void;
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
  groups = [],
  activeGroupId = null,
  onOpenGroup,
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
  const [creatingGroup, setCreatingGroup] = useState(false);
  const [profileId, setProfileId] = useState<string | null>(null);
  const [hiddenIds, setHiddenIds] = useState(readHiddenAgents);
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
  const groupedIds = useMemo(() => new Set(groups.flatMap((group) => group.members)), [groups]);
  const visibleGroups = groups.filter((group) => `${group.name} ${group.members.map((id) => agents.find((agent) => agent.agentId === id)?.name ?? "").join(" ")}`.toLowerCase().includes(query.trim().toLowerCase()));

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
      .filter((a) => a.agentId !== lead?.agentId && !groupedIds.has(a.agentId))
      .sort((a, b) => rank[a.tier] - rank[b.tier]);
    return { leadVisible: masterVisible, rows: rest };
  }, [agents, groupedIds, hiddenIds, lead, query, showHidden]);

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
        <div className="flex items-center gap-1">
        {!sample && onOpenGroup && <Button size="sm" variant="secondary" className="h-8 px-2" onClick={() => setCreatingGroup(true)} data-testid="society-create-group-button" aria-label={t("society.groups.create")} title={t("society.groups.create")}>
          <UsersRound className="h-4 w-4" aria-hidden />
        </Button>}
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
        {visibleGroups.length > 0 && <ul className="flex flex-col gap-0.5 px-2 pb-2">
          {visibleGroups.map((group) => <li key={group.group_id}>
            <button type="button" onClick={() => onOpenGroup?.(group.group_id)} aria-current={activeGroupId === group.group_id ? "true" : undefined}
              className={cn("flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", activeGroupId === group.group_id && "bg-secondary")}
              data-testid={`society-group-${group.group_id}`}>
              <span className="flex w-12 shrink-0 items-center justify-center">
                {group.members.slice(0, 3).map((id, index) => {
                  const member = agents.find((agent) => agent.agentId === id);
                  return member ? <span key={id} className={cn("rounded-full ring-2 ring-sidebar", index > 0 && "-ml-3")}><AgentSwatch agent={member} size={26} /></span> : null;
                })}
              </span>
              <span className="min-w-0 flex-1"><span className="flex items-center gap-1"><span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{group.name}</span>
                {group.last_ms && <time className="shrink-0 text-[10px] text-muted-foreground" dateTime={new Date(group.last_ms).toISOString()}>{new Date(group.last_ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</time>}
                </span>
                <span className="block truncate text-xs text-muted-foreground">{group.last_text
                  ? `${group.last_from_agent && group.last_from_agent !== "user" ? `${agents.find((agent) => agent.agentId === group.last_from_agent)?.name ?? group.last_from_agent}: ` : ""}${group.last_text}`
                  : `${t("society.groups.members")} · ${group.members.length}`}</span>
              </span>
            </button>
          </li>)}
        </ul>}
        <ul className="flex flex-col gap-0.5 px-2 pb-3">
          {loading && !leadVisible && rows.length === 0 ? (
            <li className="px-2 py-3 text-xs text-muted-foreground">{t("society.roster.loading")}</li>
          ) : null}
          {!loading && !leadVisible && rows.length === 0 && visibleGroups.length === 0 ? (
            <li className="px-2 py-3 text-xs text-muted-foreground">{t("society.roster.empty")}</li>
          ) : null}
          {rows.map((agent) => (
            <li key={agent.agentId}>
              <div
                onContextMenu={(event) => openMenu(event, agent.agentId)}
                className={cn(
                  "flex w-full items-center rounded-md px-2 text-left transition-colors hover:bg-secondary",
                  agent.agentId === activeAgentId && "bg-secondary",
                  hiddenIds.includes(agent.agentId) && "opacity-60",
                )}
              >
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
      {creatingGroup && <ChatGroupDialog agents={agents} onClose={() => setCreatingGroup(false)} onSaved={(id) => onOpenGroup?.(id)} />}
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
