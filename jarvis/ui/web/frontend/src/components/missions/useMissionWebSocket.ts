/**
 * WebSocket connection to the mission bus (`/api/missions/ws`).
 *
 * The first frame after onOpen is a "hello" with `last_seq` — the backend
 * then delivers replay events before new live events. That way a tab that
 * just reconnected can fill seq gaps without a REST catch-up.
 *
 * Reconnect happens automatically via react-use-websocket (exponential
 * backoff up to 30s, infinite retries). The UI shows "offline" via
 * store.connected.
 *
 * Mission tokens (`missions_auth.py`) live in server memory only and are
 * wiped on restart. `getUrl` obtains a fresh token before each connection,
 * while the URL itself stays token-free; the token is sent only in the first
 * hello frame.
 */
import { useCallback, useEffect, useRef } from "react";
import useWebSocket, { ReadyState } from "react-use-websocket";
import type { EventEnvelope } from "@/types/missions";
import { buildMissionSocketUrl, fetchMissionToken } from "@/lib/missionAuth";
import { useMissionsStore } from "./store";

interface ServerHello {
  type: "hello_ack";
  resumed_from: number;
}

type WsMessage = EventEnvelope | ServerHello | { type: string; [k: string]: unknown };

function isEventEnvelope(msg: WsMessage): msg is EventEnvelope {
  return (
    typeof (msg as EventEnvelope).event_id === "string" &&
    typeof (msg as EventEnvelope).mission_id === "string" &&
    typeof (msg as EventEnvelope).source_actor === "string" &&
    !!(msg as EventEnvelope).payload
  );
}

/** Give up reconnecting after this many consecutive 4401s in a row — a
 * dead token that keeps failing means something is wrong server-side, not
 * a one-off race with a restart. */
const MAX_CONSECUTIVE_AUTH_FAILURES = 3;

export function useMissionWebSocket(): { readyState: ReadyState } {
  const setConnected = useMissionsStore((s) => s.setConnected);
  const applyEvent = useMissionsStore((s) => s.applyEvent);
  const token = useRef("");
  const consecutiveAuthFailures = useRef(0);

  const getUrl = useCallback(async () => {
    token.current = await fetchMissionToken();
    return buildMissionSocketUrl("/api/missions/ws");
  }, []);

  const { sendJsonMessage, lastJsonMessage, readyState } = useWebSocket(getUrl, {
    onOpen: () => {
      consecutiveAuthFailures.current = 0;
      const lastSeq = useMissionsStore.getState().lastSeq;
      sendJsonMessage({
        type: "hello",
        last_seq: lastSeq,
        token: token.current,
      });
    },
    shouldReconnect: (event) => {
      if (event.code !== 4401) {
        consecutiveAuthFailures.current = 0;
        return true;
      }
      consecutiveAuthFailures.current += 1;
      // Beyond the cap, stop retrying: readyState settles on CLOSED and
      // store.connected stays false, which is the existing "offline"
      // signal the UI already surfaces — no new API needed.
      return consecutiveAuthFailures.current <= MAX_CONSECUTIVE_AUTH_FAILURES;
    },
    reconnectAttempts: Number.POSITIVE_INFINITY,
    retryOnError: true,
    reconnectInterval: (attempt: number) =>
      Math.min(1000 * 2 ** attempt, 30000),
    share: true,
  });

  useEffect(() => {
    setConnected(readyState === ReadyState.OPEN);
  }, [readyState, setConnected]);

  useEffect(() => {
    if (!lastJsonMessage) return;
    const msg = lastJsonMessage as WsMessage;
    if (isEventEnvelope(msg)) {
      applyEvent(msg);
    }
    // hello_ack and other control frames: silently ignore (no UI feedback needed)
  }, [lastJsonMessage, applyEvent]);

  return { readyState };
}
