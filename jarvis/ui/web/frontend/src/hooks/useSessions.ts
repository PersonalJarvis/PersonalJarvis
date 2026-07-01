/**
 * useSessions — combines a REST fetch (React Query) with live updates from
 * the WebSocket event stream.
 *
 * Strategy:
 *  - React Query holds the sessions list + detail cache.
 *  - When a VoiceSessionStarted or VoiceSessionEnded event arrives on the
 *    bus, we invalidate the list query (forces a refetch).
 *  - On VoiceSessionEnded: also invalidate the detail (aggregates are only
 *    final once the call has been hung up).
 *
 * That means no polling is needed — the list updates live the moment the
 * user hangs up.
 */
import { useEffect, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { useEventStore } from "@/store/events";
import { fetchSessionDetail, fetchSessions } from "@/components/sessions/api";

const SESSIONS_QUERY_KEY = ["sessions"] as const;

export function useSessions() {
  const queryClient = useQueryClient();
  const events = useEventStore((s) => s.events);

  const listQuery = useQuery({
    queryKey: SESSIONS_QUERY_KEY,
    queryFn: () => fetchSessions(100),
    refetchInterval: 30_000,
    retry: (failureCount, err) => {
      // 503 = recorder disabled → no retry, show the empty state right away
      if (err instanceof Error && /HTTP 503/.test(err.message)) return false;
      return failureCount < 1;
    },
  });

  // Most recent voice-session event occurrence — invalidate when it's new.
  // useMemo + dependency on events.length+last-id prevents an infinite loop.
  const lastVoiceEvent = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const ev = events[i];
      if (
        ev.name === "VoiceSessionStarted" ||
        ev.name === "VoiceSessionEnded"
      ) {
        return ev;
      }
    }
    return null;
  }, [events]);

  useEffect(() => {
    if (lastVoiceEvent === null) return;
    queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_KEY });
    // On Ended: also invalidate the detail cache of the affected entry.
    if (lastVoiceEvent.name === "VoiceSessionEnded") {
      const sid = (lastVoiceEvent.payload as { session_id?: string } | null)
        ?.session_id;
      if (typeof sid === "string" && sid.length > 0) {
        queryClient.invalidateQueries({ queryKey: ["session-detail", sid] });
      }
    }
  }, [lastVoiceEvent, queryClient]);

  return listQuery;
}

export function useSessionDetail(sessionId: string | null) {
  return useQuery({
    queryKey: ["session-detail", sessionId],
    queryFn: () => {
      if (!sessionId) throw new Error("sessionId required");
      return fetchSessionDetail(sessionId);
    },
    enabled: sessionId !== null,
  });
}
