import { useMemo } from "react";

import { AgentSwatch } from "@/components/society/AgentSwatch";
import { useSocietyRoster, type SocietyAgent } from "@/components/society/data";
import { rememberLastAgentId } from "@/views/society/lastAgent";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

/** How many agents the sidebar names before the Agents page takes over. */
export const SIDEBAR_AGENTS_MAX = 3;

/**
 * The person's three most-used agents, between the sections and the chat
 * history — each with its own face (`AgentSwatch`), one click from its chat.
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
  const roster = useSocietyRoster();
  const agents = useMemo(
    () => (roster.data && !roster.data.sample ? mostUsedAgents(roster.data.agents) : []),
    [roster.data],
  );
  if (agents.length === 0) return null;

  return (
    <section className="mt-5 px-2" aria-label={t("nav.agents")} data-testid="sidebar-agents">
      <h2 className="px-3 pb-1.5 text-sm text-muted-foreground">{t("nav.agents")}</h2>
      <ul className="space-y-px">
        {agents.map((agent) => (
          <li key={agent.agentId}>
            <button
              type="button"
              onClick={() => {
                // The Agents page opens on the agent it last showed.
                rememberLastAgentId(agent.agentId);
                setActive("agents");
              }}
              title={agent.name}
              data-testid="sidebar-agent-row"
              className="flex h-8 w-full items-center gap-3 rounded-lg px-3 text-left text-base text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
        ))}
      </ul>
    </section>
  );
}

/** The roster's own agents, most used first, capped for the sidebar. */
export function mostUsedAgents(agents: readonly SocietyAgent[]): SocietyAgent[] {
  return agents
    .filter((a) => a.tier !== "lead" && a.lifecycle !== "archived")
    .sort(
      (a, b) =>
        (b.stats?.runs ?? 0) - (a.stats?.runs ?? 0) ||
        (b.stats?.lastActiveMs ?? 0) - (a.stats?.lastActiveMs ?? 0) ||
        (b.createdMs ?? 0) - (a.createdMs ?? 0),
    )
    .slice(0, SIDEBAR_AGENTS_MAX);
}
