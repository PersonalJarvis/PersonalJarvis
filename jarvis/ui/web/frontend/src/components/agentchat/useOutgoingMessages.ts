import { useEffect, useMemo, useState } from "react";
import type { SocietyEnvelope } from "@/lib/societyApi";
import type { InternalMessageItem, TimelineItem } from "./reduce";

const MESSAGE_TYPES = new Set(["SAY", "QUERY", "ANSWER", "PROPOSE"]);
const EMPTY_EVENTS: SocietyEnvelope[] = [];

/** Retain outgoing messages and their delivery vetoes, never unrelated board activity. */
export function collectOutgoing(
  previous: SocietyEnvelope[], rows: SocietyEnvelope[], agentId: string,
): SocietyEnvelope[] {
  const messages = new Map(previous.map((row) => [row.event_id, row]));
  for (const row of rows) {
    if (row.from_agent === agentId && row.to_agent && row.to_agent !== agentId
      && row.to_agent !== "user" && MESSAGE_TYPES.has(row.msg_type)
      && typeof row.payload.text === "string" && row.payload.text.trim()) {
      messages.set(row.event_id, row);
    } else if (row.msg_type === "VETO" && row.parent_event_id && messages.has(row.parent_event_id)) {
      messages.set(row.event_id, row);
    }
  }
  return messages.size === previous.length ? previous : [...messages.values()];
}

/** The board is durable, so reopening a chat recovers replies sent via tools OR the CLI. */
export function mergeOutgoingMessages(
  items: TimelineItem[], events: SocietyEnvelope[], agentId: string, agentName: string,
  names: ReadonlyMap<string, string>,
): TimelineItem[] {
  const seen = new Set(items.filter((item) => item.type === "internal").map((item) => item.id));
  const failures = new Map(events.filter((row) => row.msg_type === "VETO")
    .map((row) => [row.parent_event_id, String(row.payload.text ?? "")]));
  const outgoing: InternalMessageItem[] = events.filter((row) => row.from_agent === agentId
    && MESSAGE_TYPES.has(row.msg_type) && row.to_agent && !seen.has(row.event_id)).map((row) => ({
    type: "internal", id: row.event_id, tsMs: row.ts_ms,
    outgoing: { recipientId: row.to_agent!, recipientName: names.get(row.to_agent!) ?? row.to_agent! },
    message: {
      message_id: row.event_id, sender_id: agentId, sender_name: agentName,
      sender_kind: "agent", text: String(row.payload.text), prompt: "", trace_id: row.trace_id,
      // The board proves submission, not delivery. The bubble labels this as sent.
      status: failures.has(row.event_id) ? "failed" : "queued", error: failures.get(row.event_id) ?? "", turn_id: "",
    },
  }));
  if (!outgoing.length) return items;
  const time = (item: TimelineItem) => item.type === "turn" ? item.startedMs : item.tsMs;
  return [...items, ...outgoing].sort((a, b) => time(a) - time(b));
}

export function useOutgoingMessages(agentId: string | null): SocietyEnvelope[] {
  const [state, setState] = useState({ agentId, events: EMPTY_EVENTS });
  useEffect(() => {
    if (!agentId) return;
    const controller = new AbortController();
    let cursor = 0;
    let timer: ReturnType<typeof setTimeout>;
    // Read ascending pages: the agent-filtered endpoint returns the newest page
    // and cannot recover a complete archive. Retain only this agent's outbox.
    const read = async () => {
      try {
        if (document.hidden) return;
        let more = true;
        while (more && !controller.signal.aborted) {
          const response = await fetch(`/api/society/events?after_seq=${cursor}&limit=1000`, { signal: controller.signal });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const data: { events: SocietyEnvelope[] } = await response.json();
          if (controller.signal.aborted) return;
          setState((previous) => {
            const events = collectOutgoing(previous.agentId === agentId ? previous.events : EMPTY_EVENTS, data.events, agentId);
            return previous.agentId === agentId && previous.events === events ? previous : { agentId, events };
          });
          const next = data.events.at(-1)?.seq ?? cursor;
          more = data.events.length === 1000 && next > cursor;
          cursor = next;
        }
      } catch (cause) {
        if (!controller.signal.aborted) console.warn("Could not load outgoing agent messages", cause);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => void read(), 3000 + Math.random() * 2000);
      }
    };
    void read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [agentId]);
  return useMemo(() => state.agentId === agentId ? state.events : EMPTY_EVENTS, [agentId, state]);
}
