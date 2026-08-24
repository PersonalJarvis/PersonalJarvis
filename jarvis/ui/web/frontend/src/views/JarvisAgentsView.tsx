/**
 * JarvisAgentsView — board of every Jarvis-Agent, live and past.
 *
 * This file is the data half. It reads TWO sources and merges them:
 *
 * - `/api/sub-agents/tree` — the live registry. Carries tool calls and a
 *   running clock, but it is an in-memory bus subscriber that drops a finished
 *   node after 60s and starts empty on every app start.
 * - `/api/missions` — the durable record of the same runs. No tool calls, but
 *   it survives, so the board is not blank whenever nothing happens to be
 *   running in this exact minute.
 *
 * Before the merge the board was live-only, which meant every number read 0
 * and the table read "no agents are running right now" even with hundreds of
 * finished runs on disk. See `missionRows.ts` for the mapping and why a run
 * present in both wins as its live node.
 *
 * All of the presentation — header, headline numbers, table and per-row
 * drilldown — lives in `DepartureBoard`, which renders it with the shared
 * `components/extensions/primitives` so this section looks like Spend, Skills,
 * Plugins, MCPs and CLIs rather than like a screen of its own.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { useSubAgentStore, type SubAgentTreeSnapshot } from "@/store/jarvisAgents";
import { DepartureBoard } from "./sub-agents/DepartureBoard";
import { selectTaskRows } from "./sub-agents/rows";
import { mergeBoardRows } from "./sub-agents/missionRows";
import { fetchMissions } from "@/components/missions/api";
import { useMissionWebSocket } from "@/components/missions/useMissionWebSocket";
import { useMissionsStore } from "@/components/missions/store";
import { useSectionHealth } from "@/hooks/useProviders";

export function JarvisAgentsView() {
  const { health } = useSectionHealth();
  const subAgents = useSubAgentStore((s) => s.subAgents);
  const sweepExpired = useSubAgentStore((s) => s.sweepExpired);
  const hydrateSnapshot = useSubAgentStore((s) => s.hydrateSnapshot);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);

  // One row per task: collapse each worker into its mission row so a single
  // dispatched task shows once (the mission "Sub-Agent" carrying the task
  // text), not twice (mission + its "Worker" child). The store keeps both
  // nodes for the DetailPanel; this only filters the displayed list. Header
  // counts and the DepartureBoard metric panel both derive from this array,
  // so they stay consistent with the rows actually shown.
  const liveRows = useMemo(() => selectTaskRows(subAgents), [subAgents]);

  // The durable half. Shares its query key with the Missions view, so opening
  // both costs one request. A failure here is not surfaced as an error: the
  // live board still works without history, and the section already has a
  // banner for the snapshot it cannot do without.
  const historyQuery = useQuery({
    queryKey: ["missions"],
    queryFn: fetchMissions,
    refetchInterval: 30_000,
  });

  const nodesList = useMemo(
    () => mergeBoardRows(liveRows, historyQuery.data?.missions ?? []),
    [liveRows, historyQuery.data],
  );

  const loadSnapshot = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await fetch("/api/sub-agents/tree", { signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const snapshot = (await res.json()) as SubAgentTreeSnapshot;
      hydrateSnapshot(snapshot);
      setSnapshotError(null);
    } catch (error) {
      if ((error as { name?: string }).name === "AbortError") return;
      setSnapshotError(error instanceof Error ? error.message : "Snapshot failed");
    }
  }, [hydrateSnapshot]);

  useEffect(() => {
    const id = setInterval(sweepExpired, 5_000);
    return () => clearInterval(id);
  }, [sweepExpired]);

  useEffect(() => {
    const controller = new AbortController();
    void loadSnapshot(controller.signal);
    const id = window.setInterval(() => void loadSnapshot(), 15_000);
    return () => {
      controller.abort();
      window.clearInterval(id);
    };
  }, [loadSnapshot]);

  // Live trigger. The board's REST snapshot (`/api/sub-agents/tree`) is the
  // source of truth — the backend SubAgentRegistry translates Phase-6 mission
  // events into the tree. After the Welle-4 migration the live events ride
  // `/api/missions/ws` as MissionDispatched/WorkerSpawned/... — names and JSON
  // shape that the legacy `SUB_AGENT_EVENT_NAMES` WS filter never matches, so
  // without this the board only refreshed on the 15s poll and felt dead during
  // an active spawn. We reuse the already-working mission stream purely as a
  // "something changed" signal and debounce a snapshot refetch, rather than
  // re-implementing the registry's reducer in TS (which would re-introduce the
  // multi-layer enum drift of BUG-008). The 15s poll above stays as a fallback.
  // Open (or share) the mission-bus WS so the board receives live mission
  // events even when the Missions view is not mounted (`share: true` in the
  // underlying hook reuses the single socket). `lastSeq` increments on every
  // mission event applied to the store, so it is our "something changed" tick.
  useMissionWebSocket();
  const missionLastSeq = useMissionsStore((s) => s.lastSeq);
  useEffect(() => {
    if (missionLastSeq === 0) return;
    const id = window.setTimeout(() => void loadSnapshot(), 350);
    return () => window.clearTimeout(id);
  }, [missionLastSeq, loadSnapshot]);

  return (
    <DepartureBoard
      agents={nodesList}
      snapshotError={snapshotError}
      health={health["subagents"] ?? null}
      historyError={historyQuery.isError}
    />
  );
}
