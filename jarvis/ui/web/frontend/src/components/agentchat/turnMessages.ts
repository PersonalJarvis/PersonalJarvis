import type { InternalMessageItem, NoticeItem, TimelineItem } from "./reduce";

/** One agent message drawn inside a turn's trace. */
export interface TraceMessage {
  id: string;
  /** Who wrote it — or, for a message Jarvis sent, who it went to. */
  name: string;
  /** True when Jarvis sent it ("→ X"); false when an agent wrote back. */
  outgoing: boolean;
  text: string;
  failed: boolean;
}

export interface AttachedTimeline {
  /** The timeline without the messages that moved into a turn. */
  items: TimelineItem[];
  /** Turn id → the messages drawn quietly inside that turn's trace. */
  messagesByTurn: Map<string, TraceMessage[]>;
}

/** Notice kinds that only repeat what an agent message already carries. */
const ECHO_KINDS = new Set(["society_message", "delegation_report"]);
/** How close a society_message notice and its agent_message arrive. */
const ECHO_WINDOW_MS = 5_000;
/** Grace after a turn's last moment in which a reply still belongs to it. */
const TURN_GRACE_MS = 2_000;

function messageOf(item: InternalMessageItem): TraceMessage {
  return {
    id: item.id,
    name: item.outgoing ? item.outgoing.recipientName : item.message.sender_name,
    outgoing: Boolean(item.outgoing),
    text: item.message.text,
    failed: item.message.status === "failed",
  };
}

function noticeMessage(item: NoticeItem): TraceMessage {
  return { id: item.id, name: item.agentName, outgoing: false, text: item.text, failed: item.status === "blocked" };
}

/**
 * Agents answering Jarvis belong to the work of the turn that asked them,
 * not below its reply (maintainer, 2026-10-03: "the output of Jarvis is
 * always at the bottom"). Every reply used to arrive twice — a
 * `society_message` notice with the full text plus the agent message itself
 * — and once more as a `delegation_report` when the turn wrapped up, each as
 * its own block after the whole turn, so the answer sat above a wall of
 * cards and had to be scrolled back to.
 *
 * Here each message that arrives while a turn is working moves into that
 * turn (drawn as one quiet trace line that opens to the full text), and the
 * echo notices that only repeat a message are dropped. A message outside any
 * turn stays where it is, still without its echo.
 */
export function attachTurnMessages(items: TimelineItem[]): AttachedTimeline {
  const messagesByTurn = new Map<string, TraceMessage[]>();
  if (!items.some((item) => item.type === "internal" || (item.type === "notice" && ECHO_KINDS.has(item.kind)))) {
    return { items, messagesByTurn };
  }
  const internals = items.filter((item): item is InternalMessageItem => item.type === "internal");
  const isEcho = (notice: NoticeItem) => notice.kind === "society_message" && internals.some((item) =>
    !item.outgoing && item.message.sender_name === notice.agentName && Math.abs(item.tsMs - notice.tsMs) <= ECHO_WINDOW_MS);

  const kept: TimelineItem[] = [];
  let turn: { id: string; endsMs: number } | null = null;
  const attach = (turnId: string, message: TraceMessage) => {
    const list = messagesByTurn.get(turnId) ?? [];
    messagesByTurn.set(turnId, [...list, message]);
  };
  for (const item of items) {
    if (item.type === "turn") {
      turn = {
        id: item.id,
        endsMs: item.status === "running" || item.durationMs === null ? Number.POSITIVE_INFINITY : item.startedMs + item.durationMs + TURN_GRACE_MS,
      };
      kept.push(item);
      continue;
    }
    if (item.type === "user") turn = null;
    const inTurn = turn !== null && item.type !== "user" && "tsMs" in item && item.tsMs <= turn.endsMs;
    if (item.type === "notice" && ECHO_KINDS.has(item.kind)) {
      if (isEcho(item)) continue;
      if (item.kind === "delegation_report" && turn !== null && inTurn) {
        // The wrap-up repeats each agent's answer; keep it only for an agent
        // whose answer did not arrive as a message of its own.
        const already = messagesByTurn.get(turn.id)?.some((m) => m.name === item.agentName && !m.outgoing);
        if (!already) attach(turn.id, noticeMessage(item));
        continue;
      }
      if (turn !== null && inTurn) {
        attach(turn.id, noticeMessage(item));
        continue;
      }
      kept.push(item);
      continue;
    }
    if (item.type === "internal" && turn !== null && inTurn) {
      attach(turn.id, messageOf(item));
      continue;
    }
    kept.push(item);
  }
  return { items: kept, messagesByTurn };
}
