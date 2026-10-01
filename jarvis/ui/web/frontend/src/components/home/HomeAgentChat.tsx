import { useEffect } from "react";
import { ArrowLeft } from "lucide-react";

import { AgentSwatch } from "@/components/society/AgentSwatch";
import { useSocietyRoster } from "@/components/society/data";
import { AgentChatPanel } from "@/components/society/chat/AgentChatPanel";
import { fill, useLocaleChunk, useT } from "@/i18n";
import { startNewTextChat } from "@/lib/newChat";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";

/**
 * One of the person's agents, talked to on the front page.
 *
 * Picked from the sidebar's agent list, the agent's own chat takes the
 * column Jarvis' chat normally holds — the very panel the Agents page shows
 * (society/chat/AgentChatPanel), so the conversation, its history and its
 * approvals are the same wherever it is opened. A slim row above names who
 * is talking and leads back to Jarvis. An agent that vanished from the
 * roster (retired elsewhere) hands the page back to Jarvis.
 */
export default function HomeAgentChat({ agentId }: { agentId: string }) {
  const t = useT();
  // The agent chat's own words live in the society locale chunk.
  useLocaleChunk("society");
  const assistantName = useEventStore((s) => s.assistantName);
  const openAgentChat = useHomeStore((s) => s.openAgentChat);
  const roster = useSocietyRoster();
  const agents = roster.data && !roster.data.sample ? roster.data.agents : [];
  const agent = agents.find((a) => a.agentId === agentId) ?? null;

  useEffect(() => {
    if (roster.isSuccess && !agent) openAgentChat(null);
  }, [agent, openAgentChat, roster.isSuccess]);

  if (!agent) {
    return (
      <div role="status" className="flex min-h-0 flex-1 items-center justify-center text-sm text-muted-foreground">
        {t("common.loading")}
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="home-agent-chat">
      <header className="flex h-12 shrink-0 items-center gap-2.5 px-4">
        <AgentSwatch agent={agent} size={22} />
        <span className="min-w-0 truncate text-sm font-medium text-foreground-strong">{agent.name}</span>
        <button
          type="button"
          onClick={() => startNewTextChat()}
          data-testid="home-agent-chat-back"
          className="ml-auto flex h-8 items-center gap-1.5 rounded-full px-3 text-xs font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ArrowLeft aria-hidden className="h-3.5 w-3.5" />
          {fill(t("home.back_to_assistant"), { name: assistantName })}
        </button>
      </header>
      {/* Full width: the panel centres its own reading column, so its
          scroll area — not a narrow box around it — meets the window edge. */}
      <div className="flex min-h-0 w-full flex-1 flex-col">
        <AgentChatPanel key={agent.agentId} agent={agent} roster={agents} />
      </div>
    </div>
  );
}
