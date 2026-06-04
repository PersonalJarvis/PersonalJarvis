// Thin client for the Chats conversation-manager REST API
// (jarvis/ui/web/chats_routes.py).
import type { ChatMessage, ConversationKind, ConversationSummary } from "@/store/events";
import type { MessageRole } from "@/types/messages";

export interface ChatTurn {
  role: string;
  text: string;
  ts_ms: number;
}

export interface ConversationDetail {
  kind: ConversationKind;
  id: string;
  title: string;
  messages: ChatTurn[];
}

export class ChatsApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ChatsApiError";
  }
}

export async function fetchConversations(days = 0): Promise<ConversationSummary[]> {
  const q = days > 0 ? `?days=${days}` : "";
  const res = await fetch(`/api/chats${q}`);
  if (!res.ok) throw new ChatsApiError("list-failed", res.status);
  return (await res.json()) as ConversationSummary[];
}

export async function resumeConversation(
  kind: ConversationKind,
  id: string,
): Promise<ConversationDetail> {
  const res = await fetch(`/api/chats/${kind}/${encodeURIComponent(id)}/resume`, {
    method: "POST",
  });
  if (!res.ok) throw new ChatsApiError("resume-failed", res.status);
  return (await res.json()) as ConversationDetail;
}

export async function speakInConversation(
  kind: ConversationKind,
  id: string,
): Promise<{ armed: boolean; seeded_turns: number }> {
  const res = await fetch(`/api/chats/${kind}/${encodeURIComponent(id)}/speak`, {
    method: "POST",
  });
  if (!res.ok) throw new ChatsApiError("speak-failed", res.status);
  return (await res.json()) as { armed: boolean; seeded_turns: number };
}

export async function deleteTextConversation(id: string): Promise<void> {
  const res = await fetch(`/api/chats/text/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new ChatsApiError("delete-failed", res.status);
}

/** Map a normalized transcript into store ChatMessages with stable ids
 *  (so the live pushMessage dedup never collides with a loaded transcript). */
export function detailToMessages(detail: ConversationDetail): ChatMessage[] {
  return detail.messages.map((m, i) => ({
    id: `hist-${detail.kind}-${detail.id}-${i}`,
    role: m.role as MessageRole,
    content: m.text,
    ts: m.ts_ms,
    thread_id: detail.id,
  }));
}
