export const CHAT_COMMAND_NAMES = [
  "help", "clear", "history", "plan", "build", "goal", "status", "stop", "continue",
  "recap", "find", "model", "remember", "message", "review", "routines",
] as const;
export type ChatCommandName = typeof CHAT_COMMAND_NAMES[number];
export type GoalStatus = "active" | "paused" | "blocked" | "complete" | "cleared";
export interface GoalState {
  id: string; objective: string; status: GoalStatus; engine: string; native_pending: boolean; reason: string;
  steps: number; stalled_steps: number; evidence: string[]; started_ms: number; updated_ms: number;
}
export interface ChatControlState {
  session_id: string; mode: "build" | "plan"; permission_mode: string; previous_permission: string; output_language: string;
  plan: string; last_request: string; goal: GoalState | null; revision: number;
  last_status: "idle" | "running" | "done" | "interrupted" | "failed";
}
export interface ChatCommand {
  name: ChatCommandName; kind: "local" | "control"; example: string; available: boolean; reason: string;
}
export interface ChatCommandResult {
  request_id: string; command: ChatCommandName; status: "done" | "started" | "failed";
  state: ChatControlState; data: Record<string, unknown>; error: string;
}
export interface ChatSearchHit { seq: number; text: string; item_id: string; session_id: string }

async function read<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchChatCommands(sessionId: string | null, signal?: AbortSignal) {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  return read<{ commands: ChatCommand[]; state: ChatControlState | null }>(await fetch(`/api/agent-chat/commands${query}`, { signal }));
}

export async function runChatCommand(sessionId: string, command: ChatCommandName, args: string, requestId: string, locale = "en", attachments: ChatAttachment[] = []) {
  return read<ChatCommandResult>(await fetch(`/api/agent-chat/sessions/${encodeURIComponent(sessionId)}/commands`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command, arguments: args, request_id: requestId, locale, attachments }),
  }));
}

/** Only a leading, exact command token typed by the user is recognized. */
export function parseChatCommand(text: string): { name: ChatCommandName; args: string } | null {
  const match = /^\/([a-z]+)(?:\s+([\s\S]*))?$/.exec(text.trim());
  if (!match || !CHAT_COMMAND_NAMES.includes(match[1] as ChatCommandName)) return null;
  return { name: match[1] as ChatCommandName, args: (match[2] ?? "").trim() };
}
import type { ChatAttachment } from "./agentChatApi";
