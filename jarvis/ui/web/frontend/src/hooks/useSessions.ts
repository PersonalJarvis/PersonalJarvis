/**
 * useSessions — kombiniert REST-Fetch (React-Query) mit Live-Updates aus
 * dem WebSocket-Event-Stream.
 *
 * Strategie:
 *  - React-Query haelt die Sessions-Liste + Detail-Cache.
 *  - Wenn auf dem Bus ein VoiceSessionStarted oder VoiceSessionEnded
 *    Event ankommt, invalidieren wir die Listen-Query (forciert Refetch).
 *  - Bei VoiceSessionEnded: zusaetzlich das Detail invalidieren (Aggregate
 *    sind erst beim Hangup final).
 *
 * Damit braucht es kein Polling — die Liste aktualisiert sich live in dem
 * Moment, in dem der User auflegt.
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
      // 503 = Recorder disabled → kein Retry, sofort Empty-State zeigen
      if (err instanceof Error && /HTTP 503/.test(err.message)) return false;
      return failureCount < 1;
    },
  });

  // Letztes Voice-Session-Event-Vorkommen — wenn neu, invalidieren.
  // useMemo + Dependency auf events.length+letzte-id verhindert Endlos-Loop.
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
    // Bei Ended: Detail-Cache des betroffenen Eintrags ebenfalls invalidieren.
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
