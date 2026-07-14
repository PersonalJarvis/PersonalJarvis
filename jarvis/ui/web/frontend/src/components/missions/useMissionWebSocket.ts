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
 * Auth tokens (`missions_auth.py`) live in server memory only and are
 * wiped on restart, so a `window.__JARVIS_TOKEN` captured before a restart
 * closes the socket with 4401. `getUrl` is passed as a function (not a
 * plain string) so react-use-websocket re-resolves it on every reconnect
 * attempt, including scheduled ones — a 4401 close flags the next
 * resolution to fetch a fresh token first (mirrors the fallback in
 * WorkspaceTerminal.tsx's `getToken()`).
 */
import { useCallback, useEffect, useRef } from "react";
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

/** Give up reconnecting after this many consecutive 4401s in a row — a
 * dead token that keeps failing means something is wrong server-side, not
 * a one-off race with a restart. */
const MAX_CONSECUTIVE_AUTH_FAILURES = 3;

async function fetchFreshToken(): Promise<string> {
  try {
    const res = await fetch("/api/missions/auth/token");
    const body = (await res.json()) as { token?: string };
    const token = body.token ?? "";
    window.__JARVIS_TOKEN = token;
    return token;
  } catch {
    return window.__JARVIS_TOKEN ?? "";
  }
}

export function useMissionWebSocket(): { readyState: ReadyState } {
  const setConnected = useMissionsStore((s) => s.setConnected);
  const applyEvent = useMissionsStore((s) => s.applyEvent);
  const needsFreshToken = useRef(false);
  const consecutiveAuthFailures = useRef(0);

  const getUrl = useCallback(async () => {
    const token = needsFreshToken.current
      ? await fetchFreshToken()
      : (window.__JARVIS_TOKEN ?? "");
    needsFreshToken.current = false;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const host = window.location.host;
    const query = token ? `?token=${encodeURIComponent(token)}` : "";
    return `${proto}://${host}/api/missions/ws${query}`;
  }, []);

  const { sendJsonMessage, lastJsonMessage, readyState } = useWebSocket(getUrl, {
    onOpen: () => {
      consecutiveAuthFailures.current = 0;
      const lastSeq = useMissionsStore.getState().lastSeq;
      sendJsonMessage({
        type: "hello",
        last_seq: lastSeq,
        token: window.__JARVIS_TOKEN ?? "",
      });
    },
    onClose: (event) => {
      if (event.code === 4401) {
        needsFreshToken.current = true;
      }
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
