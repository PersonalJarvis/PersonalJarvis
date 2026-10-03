/**
 * The agent's own conversations with Jarvis and its teammates.
 *
 * When Jarvis or another agent sends this agent something, the agent works on
 * it in a separate chat (`society:<agent>:with:<sender>`) — never in the
 * person's chat with the agent. This strip lists those chats and opens one in
 * the card's chat lane as a full chat: the whole work trace, approvals and a
 * composer to step in.
 */
import { useEffect, useState } from "react";
import { MessagesSquare } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useRoutineNavigation } from "./routineNavigation";

export interface AgentConversation {
  session_id: string;
  title: string;
  updated_ms: number;
  running: boolean;
  /** An approval card or a question in this chat waits for the person. */
  waiting?: boolean;
  owner_id: string;
  counterpart_id: string;
  counterpart_name: string;
}

export async function fetchAgentConversations(agentId: string, signal?: AbortSignal): Promise<AgentConversation[]> {
  const response = await fetch(`/api/society/agents/${encodeURIComponent(agentId)}/conversations`, { signal });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data: { conversations?: AgentConversation[] } = await response.json();
  return data.conversations ?? [];
}

/**
 * Keep the list fresh without hammering the backend: one jittered request every
 * 8-12 s. Not gated on `document.hidden` — WebView2 misreports it.
 */
function useAgentConversations(agentId: string): AgentConversation[] {
  const [state, setState] = useState<{ agentId: string; rows: AgentConversation[] }>({ agentId, rows: [] });
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      try {
        const rows = await fetchAgentConversations(agentId, controller.signal);
        if (!controller.signal.aborted) setState({ agentId, rows });
      } catch (cause) {
        if (!controller.signal.aborted) console.warn("Could not load the agent's conversations", cause);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => void read(), 8000 + Math.random() * 4000);
      }
    };
    void read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [agentId]);
  return state.agentId === agentId ? state.rows : [];
}

export function AgentConversationsBar({ agentId, agentName, displayName }: {
  agentId: string;
  agentName: string;
  /** The name a person sees for a roster id (the lead follows the wake word). */
  displayName: (id: string, fallback: string) => string;
}) {
  const t = useT();
  const open = useRoutineNavigation((state) => state.open);
  const rows = useAgentConversations(agentId);
  if (!rows.length) return null;
  return (
    <div
      className="flex shrink-0 items-center gap-2 overflow-x-auto border-b border-border px-3 py-1.5"
      data-testid="agent-conversations"
      title={t("society.conversations.hint")}
    >
      <span className="flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground">
        <MessagesSquare className="h-3.5 w-3.5" aria-hidden />
        {t("society.conversations.label")}
      </span>
      {rows.map((row) => {
        const name = displayName(row.counterpart_id, row.counterpart_name);
        return (
          <button
            key={row.session_id}
            type="button"
            data-testid="agent-conversation"
            data-session-id={row.session_id}
            onClick={() => open({
              agentId,
              sessionId: row.session_id,
              kind: "conversation",
              // The owner's name first: the lead's list opens the agents' chats with it.
              title: row.owner_id === agentId
                ? `${agentName} · ${name}`
                : `${name} · ${agentName}`,
              timestamp: row.updated_ms,
            })}
            data-state={row.waiting ? "waiting" : row.running ? "running" : "idle"}
            title={row.waiting ? t("society.conversations.waiting") : undefined}
            className={cn(
              "flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              row.waiting ? "border-warning text-warning" : "border-border text-foreground",
            )}
          >
            <span
              aria-hidden
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                row.waiting ? "bg-warning" : row.running ? "bg-success" : "bg-muted-foreground/50",
              )}
            />
            {t("society.conversations.with").replace("{0}", name)}
            {row.waiting ? <span className="sr-only">{t("society.conversations.waiting")}</span>
              : row.running ? <span className="sr-only">{t("society.conversations.running")}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
