import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { WSClient } from "@/lib/ws";

/**
 * Live-reload hook for the desktop wiki view.
 *
 * Mounts a WebSocket connection to `/api/wiki/live` and invalidates all
 * React Query caches that project vault state every time the server connects
 * or pushes a `page_changed` message.
 *
 * Mount this hook once at the WikiView level. Switching away from the wiki
 * tab unmounts the hook and closes the socket, so no background connection
 * remains while the user is on another tab.
 *
 * The shared transport handles reconnect backoff and the one-time-ticket
 * authentication fallback required by Safari and WKWebView.
 */
export interface UseWikiLiveResult {
  /** True while the WebSocket is open. */
  connected: boolean;
  /** Wall-clock milliseconds of the last forwarded event, or null. */
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
  const message = value as Record<string, unknown>;
  return (
    message.type === "page_changed" &&
    typeof message.slug === "string" &&
    typeof message.path === "string" &&
    (message.kind === "created" ||
      message.kind === "modified" ||
      message.kind === "deleted")
  );
}

function buildWsUrl(): string {
  // Derive the URL from the current host so the Vite proxy and packaged
  // desktop launcher work without separate configuration.
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}/api/wiki/live`;
}

export function useWikiLive(): UseWikiLiveResult {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);

  useEffect(() => {
    let active = true;

    const invalidateWikiProjection = () => {
      // Prefix invalidation is intentional for pages, searches, and backlinks.
      // A changed source page can add links to several targets, so refreshing
      // backlinks only for that source leaves the target panels stale.
      for (const queryKey of [
        ["wiki", "tree"],
        ["wiki", "graph"],
        ["wiki", "health"],
        ["wiki", "search"],
        ["wiki", "page"],
        ["wiki", "backlinks"],
      ]) {
        void queryClient.invalidateQueries({ queryKey });
      }
    };

    const client = new WSClient({
      url: buildWsUrl(),
      onOpen: () => {
        if (!active) return;
        setConnected(true);
        // Recover any events missed while the socket was disconnected.
        invalidateWikiProjection();
      },
      onMessage: (message) => {
        if (!active || !isPageChanged(message)) return;
        invalidateWikiProjection();
        setLastEventAt(Date.now());
      },
      onClose: () => {
        if (!active) return;
        setConnected(false);
      },
    });

    client.connect();

    return () => {
      active = false;
      client.close();
    };
  }, [queryClient]);

  return { connected, lastEventAt };
}
