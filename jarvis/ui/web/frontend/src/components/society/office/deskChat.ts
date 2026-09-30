/**
 * What a desk monitor shows: the tail of the agent's own chat, reduced to a
 * few short lines — who said what, and which tool is running. Pure, tested.
 */
import type { AgentChatEvent } from "@/lib/agentChatApi";

export type ChatLineKind = "user" | "agent" | "tool" | "notice" | "error";

export interface ChatLine { kind: ChatLineKind; text: string }

/** Lines kept per monitor; the screen shows the newest that fit. */
export const MAX_CHAT_LINES = 14;

function clean(text: unknown, max = 160): string {
  if (typeof text !== "string") return "";
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** Short, human tool names: "mcp__jarvis__society_shell" → "society_shell". */
export function toolLabel(name: unknown): string {
  if (typeof name !== "string" || !name) return "tool";
  const parts = name.split("__");
  return parts[parts.length - 1] || name;
}

export function chatLines(events: readonly AgentChatEvent[]): ChatLine[] {
  const lines: ChatLine[] = [];
  for (const event of events) {
    const p = event.payload ?? {};
    let line: ChatLine | null = null;
    switch (event.kind) {
      case "user_message": line = { kind: "user", text: clean(p.text) }; break;
      case "agent_message": line = { kind: p.sender_kind === "user" ? "user" : "agent", text: clean(p.text) }; break;
      case "assistant_text": line = { kind: "agent", text: clean(p.text) }; break;
      case "tool_call": line = { kind: "tool", text: clean(typeof p.summary === "string" && p.summary ? p.summary : toolLabel(p.name), 80) }; break;
      case "notice": line = { kind: "notice", text: clean(p.text ?? p.summary, 100) }; break;
      case "error": line = { kind: "error", text: clean(p.message, 100) }; break;
      default: break;
    }
    if (line && line.text) lines.push(line);
  }
  return lines.slice(-MAX_CHAT_LINES);
}

/** A cheap identity for "did anything change": last event seq and count. */
export function chatVersion(events: readonly AgentChatEvent[]): string {
  const last = events[events.length - 1];
  return last ? `${last.seq}:${events.length}` : "0";
}
