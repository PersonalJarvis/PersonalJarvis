import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

/**
 * Live-reload hook for the desktop wiki view.
 *
 * Mounts a WebSocket connection to `/api/wiki/live` and invalidates the
 * four React Query caches that depend on vault state every time the
 * server pushes a `page_changed` message.
 *
 * Mount this hook **once** at the WikiView level (not deeper). Switching
 * away from the wiki tab unmounts the hook and closes the socket — that
 * is the desired behaviour: no background polling when the user is on
 * another tab.
 *
 * Reconnects use exponential backoff starting at 1 s and capped at
 * 30 s so a flapping server cannot DOS itself.
 */
export interface UseWikiLiveResult {
  /** True while the WS is OPEN. False during connect, reconnect, or after unmount. */
  connected: boolean;
  /** Wall-clock ms of the last forwarded `page_changed` event, or null. */
  lastEventAt: number | null;
}

interface PageChangedMessage {
  type: "page_changed";
  slug: string;
  path: string;
  kind: "created" | "modified" | "deleted";
}

function isPageChanged(value: unknown): value is PageChangedMessage {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    v.type === "page_changed" &&
    typeof v.slug === "string" &&
    typeof v.path === "string" &&
    (v.kind === "created" || v.kind === "modified" || v.kind === "deleted")
  );
}

function buildWsUrl(): string {
  // Always derive from the current host so dev (Vite proxy) and prod
  // (pywebview/launcher on 47821) both work without config.
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/api/wiki/live`;
}

export function useWikiLive(): UseWikiLiveResult {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);

  // Refs so the effect deps stay stable (we only re-run on qc change).
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    const url = buildWsUrl();

    const scheduleReconnect = () => {
      if (!mountedRef.current) return;
      // Exponential backoff capped at 30 s, with a small floor at 250 ms
      // so the first immediate retry on a flap is still snappy.
      const attempt = reconnectAttemptsRef.current;
      const delayMs = Math.min(30_000, Math.max(250, 1000 * Math.pow(2, attempt)));
      reconnectAttemptsRef.current = attempt + 1;
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        connect();
      }, delayMs);
    };

    const connect = () => {
      if (!mountedRef.current) return;
      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch {
        // Constructor itself may throw on a malformed URL (jsdom edge
        // cases). Treat as a close.
        scheduleReconnect();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        reconnectAttemptsRef.current = 0;
        setConnected(true);
      };

      ws.onmessage = (ev: MessageEvent) => {
        if (!mountedRef.current) return;
        let parsed: unknown;
        try {
          parsed = JSON.parse(typeof ev.data === "string" ? ev.data : "");
        } catch {
          return;
        }
        if (!isPageChanged(parsed)) return;
        // Invalidate the four queries that depend on vault state. The
        // keys match the contract documented in 00-OVERVIEW.md §4.
        qc.invalidateQueries({ queryKey: ["wiki", "tree"] });
        qc.invalidateQueries({ queryKey: ["wiki", "page", parsed.slug] });
        qc.invalidateQueries({ queryKey: ["wiki", "graph"] });
        qc.invalidateQueries({ queryKey: ["wiki", "backlinks", parsed.slug] });
        setLastEventAt(Date.now());
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setConnected(false);
        wsRef.current = null;
        scheduleReconnect();
      };

      ws.onerror = () => {
        // Some browsers fire error then close; some only fire error.
        // Force a close so the onclose path runs.
        try {
          ws.close();
        } catch {
          /* noop */
        }
      };
    };

    connect();

    return () => {
      mountedRef.current = false;
      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      const ws = wsRef.current;
      if (ws !== null) {
        // Detach onclose first so the cleanup doesn't trigger a reconnect.
        ws.onclose = null;
        ws.onerror = null;
        try {
          ws.close();
        } catch {
          /* noop */
        }
        wsRef.current = null;
      }
    };
  }, [qc]);

  return { connected, lastEventAt };
}
