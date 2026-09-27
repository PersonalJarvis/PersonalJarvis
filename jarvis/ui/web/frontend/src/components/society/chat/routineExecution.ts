import type { AgentChatEvent } from "@/lib/agentChatApi";

export interface RoutineChatTarget {
  agentId: string;
  sessionId: string;
  title: string;
  timestamp: number;
  result?: string;
  legacy?: {
    taskId: string;
    messageId?: string;
    startedMs?: number;
    finishedMs?: number;
  };
}

/** Only a complete scheduler envelope is a routine, never a similar user message. */
const envelope = /^Scheduled routine ([^\s.]+)\. Follow your CURRENT standing instructions(?: and permissions)?\.\r?\n(?:This execution has its own background chat with bypass permissions\.\r?\n)?Use your memory and conversation archive for prior results\. For information watches, check sources and dates, remember last-seen items, and report only meaningful new findings\.\r?\n\r?\n/;

export function routineTask(text: string): string | null {
  const match = envelope.exec(text);
  return match ? text.slice(match[0].length).trim() : null;
}

export function routineTaskId(text: string): string | null {
  return envelope.exec(text)?.[1] ?? null;
}

/** Read the exact old turn; never substitute the latest turn or the whole main chat. */
export function legacyRoutineEvents(events: AgentChatEvent[], target: RoutineChatTarget): AgentChatEvent[] {
  const legacy = target.legacy;
  if (!legacy) return [];
  const matches: AgentChatEvent[][] = [];
  for (let index = 0; index < events.length; index += 1) {
    const message = events[index];
    if (message.kind !== "user_message" || routineTaskId(String(message.payload.text ?? "")) !== legacy.taskId) continue;
    if (legacy.messageId && legacy.messageId !== `u-${message.seq || message.ts_ms}`) continue;
    let next = index + 1;
    while (next < events.length && events[next].kind !== "user_message") next += 1;
    const span = events.slice(index + 1, next);
    const turn = span.find((event) => event.kind === "turn_started");
    if (!turn?.payload.turn_id) continue;
    const own = span.filter((event) => event.payload.turn_id === turn.payload.turn_id);
    if (!legacy.messageId) {
      // Task steps and chat messages share the local clock. A small allowance
      // covers timestamp rounding, not another daily run of the same prompt.
      if (legacy.startedMs !== undefined) {
        if (message.ts_ms < legacy.startedMs - 2000) continue;
        if (legacy.finishedMs === undefined || message.ts_ms > legacy.finishedMs + 2000) continue;
      } else {
        const finished = own.find((event) => event.kind === "turn_finished");
        if (!finished || legacy.finishedMs === undefined || Math.abs(finished.ts_ms - legacy.finishedMs) > 5000) continue;
      }
    }
    matches.push([message, ...own]);
  }
  // Ambiguous timestamps are not permission to show a different execution.
  return matches.length === 1 ? matches[0] : [];
}
