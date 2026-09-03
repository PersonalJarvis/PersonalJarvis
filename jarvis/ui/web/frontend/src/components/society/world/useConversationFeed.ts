/**
 * The island's ear: `SocietyMessageSent` and `SocietyRoomChanged` off the
 * app-wide WebSocket, into the conversation store.
 *
 * Modelled on `questsData.ts`, with one difference that matters. That hook
 * peeks at `events[0]` and invalidates a query — losing the middle of a burst
 * costs nothing there, because the refetch sees the final state anyway. Here a
 * lost message is a lost sentence, so this DRAINS: it keeps a cursor on the
 * last id it handled and replays everything above it, oldest first.
 *
 * Nothing is fetched and nothing is polled. When the island is not drawing —
 * reduced motion, no WebGL, another section — the subscription is dropped and
 * the store is emptied, so an idle society costs exactly nothing (MASTERPLAN
 * §2.8).
 */
import { useEffect, useRef } from "react";

import { useEventStore } from "@/store/events";
import type { SocietyAgent } from "../data";
import {
  useConversationStore,
  type IncomingMessage,
  type IncomingRoom,
} from "./conversationStore";

const MESSAGE_EVENT = "SocietyMessageSent";
const ROOM_EVENT = "SocietyRoomChanged";

/**
 * How far back to walk when the cursor is not in the log any more. The store
 * keeps 500 events and a conversation is seconds old, so a cursor that fell
 * off the end means we were away — replaying hundreds of stale lines would
 * bubble a conversation that finished minutes ago.
 */
const MAX_CATCHUP = 64;

function asMessage(payload: unknown): IncomingMessage | null {
  if (!payload || typeof payload !== "object") return null;
  const p = payload as Record<string, unknown>;
  const from = typeof p.from_agent === "string" ? p.from_agent : "";
  if (!from) return null;
  return {
    eventId: typeof p.event_id === "string" ? p.event_id : "",
    seq: typeof p.seq === "number" ? p.seq : 0,
    msgType: typeof p.msg_type === "string" ? p.msg_type : "SAY",
    fromAgent: from,
    toAgent: typeof p.to_agent === "string" ? p.to_agent : "",
    roomId: typeof p.room_id === "string" ? p.room_id : "",
    roomRound: typeof p.room_round === "number" ? p.room_round : 0,
    text: typeof p.text === "string" ? p.text : "",
    textChars: typeof p.text_chars === "number" ? p.text_chars : 0,
    truncated: p.truncated === true,
  };
}

function asRoom(payload: unknown): IncomingRoom | null {
  if (!payload || typeof payload !== "object") return null;
  const p = payload as Record<string, unknown>;
  const roomId = typeof p.room_id === "string" ? p.room_id : "";
  if (!roomId) return null;
  return {
    roomId,
    phase: typeof p.phase === "string" ? p.phase : "",
    members: Array.isArray(p.members) ? p.members.map(String) : [],
    topic: typeof p.topic === "string" ? p.topic : "",
    reason: typeof p.reason === "string" ? p.reason : "",
    maxRounds: typeof p.max_rounds === "number" ? p.max_rounds : 0,
  };
}

/**
 * Subscribe the conversation store to the live board while `enabled`.
 *
 * `agents` is the roster the island is drawing: an id that is not in it has no
 * figure, so a message from the scheduler or to an archived row is either
 * one-sided or dropped rather than drawn as a beam into empty water.
 */
export function useConversationFeed(agents: SocietyAgent[], enabled: boolean): void {
  // The roster lives in a ref, not in the subscription's closure: it arrives a
  // moment after the island mounts, and a subscription rebuilt on every roster
  // refetch would reset its cursor and replay whatever was in flight.
  const known = useRef<Set<string>>(new Set());
  known.current = new Set(agents.map((a) => a.agentId));

  useEffect(() => {
    const store = useConversationStore.getState();
    if (!enabled) {
      store.reset();
      return;
    }
    let cursor: string | null = useEventStore.getState().events[0]?.id ?? null;

    return useEventStore.subscribe((state) => {
      const log = state.events;
      const top = log[0];
      if (!top || top.id === cursor) return;

      // The log is newest-first: collect everything above the cursor, then
      // replay it in the order it happened.
      const fresh = [];
      for (let i = 0; i < log.length && i < MAX_CATCHUP; i++) {
        if (log[i].id === cursor) break;
        fresh.push(log[i]);
      }
      cursor = top.id;

      const convo = useConversationStore.getState();
      for (let i = fresh.length - 1; i >= 0; i--) {
        const item = fresh[i];
        if (item.name === MESSAGE_EVENT) {
          const message = asMessage(item.payload);
          if (message) convo.noteMessage(message, known.current);
        } else if (item.name === ROOM_EVENT) {
          const room = asRoom(item.payload);
          if (room) convo.noteRoom(room);
        }
      }
    });
  }, [enabled]);

  useEffect(() => () => useConversationStore.getState().reset(), []);
}
