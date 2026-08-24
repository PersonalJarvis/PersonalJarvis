// Thin client for the Chats conversation-manager REST API
// (jarvis/ui/web/chats_routes.py).
import type { ChatMessage, ConversationKind, ConversationSummary } from "@/store/events";
import {
  replayTrace,
  type StoredTrace,
  type ThinkingTraceSnapshot,
} from "@/lib/thinkingSteps";
import type { MessageRole } from "@/types/messages";

export interface ChatTurn {
  role: string;
  text: string;
  ts_ms: number;
  /** The stored reasoning trace behind an assistant reply, when one was kept. */
  trace?: StoredTrace | null;
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

/**
 * The voice/text history. `limit` is deliberately generous: the sidebar shows
 * a handful, but the "All chats" archive promises everything, and a cap that
 * quietly hides old conversations reads as data loss.
 */
export async function fetchConversations(days = 0, limit = 500): Promise<ConversationSummary[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (days > 0) params.set("days", String(days));
  const res = await fetch(`/api/chats?${params.toString()}`);
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

/**
 * Begin a fresh voice run: the brain forgets the thread it was seeded with,
 * and a session that is still live is ended so the next wake word opens a new
 * one. The microphone is not re-armed — starting to talk stays the user's move.
 */
export async function startNewVoiceRun(): Promise<{
  cleared: boolean;
  ended: boolean;
}> {
  const res = await fetch("/api/chats/voice/new", { method: "POST" });
  if (!res.ok) throw new ChatsApiError("new-voice-run-failed", res.status);
  return (await res.json()) as { cleared: boolean; ended: boolean };
}

export async function deleteTextConversation(id: string): Promise<void> {
  const res = await fetch(`/api/chats/text/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new ChatsApiError("delete-failed", res.status);
}

/** The stable id of the i-th message of a loaded conversation. */
function historyMessageId(detail: ConversationDetail, index: number): string {
  return `hist-${detail.kind}-${detail.id}-${index}`;
}

/** Map a normalized transcript into store ChatMessages with stable ids
 *  (so the live pushMessage dedup never collides with a loaded transcript). */
export function detailToMessages(detail: ConversationDetail): ChatMessage[] {
  return detail.messages.map((m, i) => {
    const message: ChatMessage = {
      id: historyMessageId(detail, i),
      role: m.role as MessageRole,
      content: m.text,
      ts: m.ts_ms,
      thread_id: detail.id,
    };
    const trace = m.role === "assistant" && m.trace ? replayTrace(m.trace) : null;
    if (trace) message.trace = trace;
    return message;
  });
}

/**
 * The stored traces of a loaded conversation, keyed by the same ids
 * `detailToMessages` hands out — replayed through the live reducer so the
 * history shows "Thought for 4s" + steps exactly like the live turn did.
 */
export function detailToTraces(
  detail: ConversationDetail,
): Record<string, ThinkingTraceSnapshot> {
  const out: Record<string, ThinkingTraceSnapshot> = {};
  detail.messages.forEach((m, i) => {
    if (m.role !== "assistant" || !m.trace) return;
    const snapshot = replayTrace(m.trace);
    if (snapshot) out[historyMessageId(detail, i)] = snapshot;
  });
  return out;
}
