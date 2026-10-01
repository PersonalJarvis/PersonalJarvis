import { useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";

import { AgentSwatch } from "@/components/society/AgentSwatch";
import { useSocietyRoster, type SocietyAgent } from "@/components/society/data";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";

/** How many agents the sidebar names before "Show all" folds the rest out. */
export const SIDEBAR_AGENTS_MAX = 3;

/**
 * The person's agents, most used first, between the sections and the chat
 * history — each with its own face (`AgentSwatch`).
 *
 * Three show at rest; "Show all" folds out the rest. A click opens that
 * agent's own chat on the front page, in the column Jarvis' chat normally
 * holds (components/home/HomeAgentChat), rather than leaving for the Agents
 * page; the open one is lit like the open chat in the history below.
 *
 * "Most used" is the roster's own numbers: runs first, then the most recent
 * activity, then the newest. Jarvis itself (the lead) is the front page, so
 * it is not listed again; archived agents and the sample roster a fresh
 * install shows never appear. With no agents of their own the section is
 * absent rather than an empty heading.
 */
export default function SidebarAgents() {
  const t = useT();
  const setActive = useEventStore((s) => s.setActiveSection);
  const onFrontPage = useEventStore((s) => s.activeSection === "chats");
  const openId = useHomeStore((s) => s.agentChatId);
  const openAgentChat = useHomeStore((s) => s.openAgentChat);
  const [expanded, setExpanded] = useState(false);
  const roster = useSocietyRoster();
  const all = useMemo(
    () => (roster.data && !roster.data.sample ? rankAgents(roster.data.agents) : []),
    [roster.data],
  );
  if (all.length === 0) return null;
  const shown = expanded ? all : all.slice(0, SIDEBAR_AGENTS_MAX);
  const hidden = all.length - SIDEBAR_AGENTS_MAX;

  return (
    <section className="mt-5 px-2" aria-label={t("nav.agents")} data-testid="sidebar-agents">
      <h2 className="px-3 pb-1.5 text-sm text-muted-foreground">{t("nav.agents")}</h2>
      <ul className="space-y-px">
        {shown.map((agent) => {
          const active = onFrontPage && openId === agent.agentId;
          return (
            <li key={agent.agentId}>
              <button
                type="button"
                onClick={() => {
                  openAgentChat(agent.agentId);
                  setActive("chats");
                }}
                title={agent.name}
                aria-current={active ? "page" : undefined}
                data-testid="sidebar-agent-row"
                className={cn(
                  "flex h-8 w-full items-center gap-3 rounded-lg px-3 text-left text-base transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  active ? "jarvis-nav-active bg-secondary text-foreground-strong" : "text-foreground hover:bg-secondary",
                )}
              >
                <span className="-ml-0.5 flex h-5 w-5 shrink-0 items-center justify-center">
                  <AgentSwatch agent={agent} size={20} />
                </span>
                <span className="min-w-0 flex-1 truncate">{agent.name}</span>
                {agent.state === "working" && (
                  <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent animate-jarvis-pulse" />
                )}
              </button>
            </li>
          );
        })}
      </ul>
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          data-testid="sidebar-agents-more"
          className="flex h-8 w-full items-center gap-3 rounded-lg px-3 text-left text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ChevronDown
            aria-hidden
            strokeWidth={1.75}
            className={cn("h-[18px] w-[18px] shrink-0 transition-transform", expanded && "rotate-180")}
          />
          <span className="min-w-0 flex-1 truncate text-sm">{t(expanded ? "sidebar.show_less" : "sidebar.show_all")}</span>
          {!expanded && <span className="shrink-0 text-sm tabular-nums text-foreground-faint">+{hidden}</span>}
        </button>
      )}
    </section>
  );
}

/** The roster's own agents, most used first. */
export function rankAgents(agents: readonly SocietyAgent[]): SocietyAgent[] {
  return agents
    .filter((a) => a.tier !== "lead" && a.lifecycle !== "archived")
    .sort(
      (a, b) =>
        (b.stats?.runs ?? 0) - (a.stats?.runs ?? 0) ||
        (b.stats?.lastActiveMs ?? 0) - (a.stats?.lastActiveMs ?? 0) ||
        (b.createdMs ?? 0) - (a.createdMs ?? 0),
    );
}

/** The ones the sidebar shows at rest. */
export function mostUsedAgents(agents: readonly SocietyAgent[]): SocietyAgent[] {
  return rankAgents(agents).slice(0, SIDEBAR_AGENTS_MAX);
}
