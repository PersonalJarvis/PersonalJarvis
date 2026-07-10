import { useEffect, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { useEventStore } from "@/store/events";
import { fetchRunDetail, fetchRuns } from "@/components/runs/api";

const RUNS_QUERY_KEY = ["runs"] as const;

export function useRuns() {
  const queryClient = useQueryClient();
  const events = useEventStore((s) => s.events);

  const listQuery = useQuery({
    queryKey: RUNS_QUERY_KEY,
    queryFn: () => fetchRuns(100),
    refetchInterval: 30_000,
    retry: (failureCount, err) => {
      if (err instanceof Error && /HTTP 503/.test(err.message)) return false;
      return failureCount < 1;
    },
  });

  const lastBoundary = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const ev = events[i];
      if (ev.name === "VoiceSessionStarted" || ev.name === "VoiceSessionEnded") return ev;
    }
    return null;
  }, [events]);

  useEffect(() => {
    if (lastBoundary === null) return;
    queryClient.invalidateQueries({ queryKey: RUNS_QUERY_KEY });
    if (lastBoundary.name === "VoiceSessionEnded") {
      const sid = (lastBoundary.payload as { session_id?: string } | null)?.session_id;
      if (typeof sid === "string" && sid.length > 0) {
        queryClient.invalidateQueries({ queryKey: ["run-detail", sid] });
      }
    }
  }, [lastBoundary, queryClient]);

  return listQuery;
}

const LIVE_KINDS = new Set([
  "VoiceTurnStarted", "VoiceTurnCompleted", "RealtimeSessionReady",
  "TranscriptFinal", "IntentClassified",
  "ActionProposed", "ActionApproved", "ActionDenied", "BrainTurnStarted",
  "BrainTurnCompleted", "ResponseGenerated", "SystemStateChanged", "LatencySpan",
  "ErrorOccurred",
]);

export function useRunDetail(sessionId: string | null) {
  const queryClient = useQueryClient();
  const events = useEventStore((s) => s.events);

  const query = useQuery({
    queryKey: ["run-detail", sessionId],
    queryFn: () => {
      if (!sessionId) throw new Error("sessionId required");
      return fetchRunDetail(sessionId);
    },
    enabled: sessionId !== null,
  });

  const lastLive = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (LIVE_KINDS.has(events[i].name)) return events[i];
    }
    return null;
  }, [events]);

  useEffect(() => {
    if (sessionId === null || lastLive === null) return;
    const sid = (lastLive.payload as { session_id?: string } | null)?.session_id;
    if (sid === sessionId) {
      queryClient.invalidateQueries({ queryKey: ["run-detail", sessionId] });
    }
  }, [lastLive, sessionId, queryClient]);

  return query;
}
