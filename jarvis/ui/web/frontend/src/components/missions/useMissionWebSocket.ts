/**
 * WebSocket-Verbindung zum Mission-Bus (`/api/missions/ws`).
 *
 * Erst-Frame nach onOpen ist ein "hello" mit `last_seq` — das Backend liefert
 * dann Replay-Events vor neuen Live-Events. So kann ein gerade reconnecteter
 * Tab seq-Luecken auffuellen, ohne ein REST-Catch-up.
 *
 * Reconnect erfolgt automatisch via react-use-websocket (exponentielles
 * Backoff bis 30s, infinite Retries). UI zeigt "offline" via store.connected.
 */
import { useEffect, useMemo } from "react";
import useWebSocket, { ReadyState } from "react-use-websocket";
import type { EventEnvelope } from "@/types/missions";
import { useMissionsStore } from "./store";

declare global {
  interface Window {
    __JARVIS_TOKEN?: string;
  }
}

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

export function useMissionWebSocket(): { readyState: ReadyState } {
  const setConnected = useMissionsStore((s) => s.setConnected);
  const applyEvent = useMissionsStore((s) => s.applyEvent);

  const wsUrl = useMemo(() => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const host = window.location.host;
    const token = window.__JARVIS_TOKEN;
    const query = token ? `?token=${encodeURIComponent(token)}` : "";
    return `${proto}://${host}/api/missions/ws${query}`;
  }, []);

  const { sendJsonMessage, lastJsonMessage, readyState } = useWebSocket(wsUrl, {
    onOpen: () => {
      const lastSeq = useMissionsStore.getState().lastSeq;
      sendJsonMessage({
        type: "hello",
        last_seq: lastSeq,
        token: window.__JARVIS_TOKEN ?? "",
      });
    },
    shouldReconnect: () => true,
    reconnectAttempts: Number.POSITIVE_INFINITY,
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
    // hello_ack und andere Control-Frames: silent ignore (kein UI-Feedback noetig)
  }, [lastJsonMessage, applyEvent]);

  return { readyState };
}
