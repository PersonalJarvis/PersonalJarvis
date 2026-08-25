/**
 * JarvisAgentsView — board of every Jarvis-Agent, live and past.
 *
 * This file is the data half. It reads THREE sources and merges them:
 *
 * - `/api/sub-agents/tree` — the live registry. Carries tool calls and a
 *   running clock, but it is an in-memory bus subscriber that drops a finished
 *   node after 60s and starts empty on every app start.
 * - `/api/missions` — the durable record of the same runs. No tool calls, but
 *   it survives, so the board is not blank whenever nothing happens to be
 *   running in this exact minute.
 * - `/api/outputs` — the archive of output directories. It knows a run's
 *   terminal reason, one-line summary and the slug its deliverables live
 *   under, which is what turns "Failed" into "provider quota exhausted" on the
 *   board and what the insight page opens the Artifacts section with.
 *
 * Before the merge the board was live-only, which meant every number read 0
 * and the table read "no agents are running right now" even with hundreds of
 * finished runs on disk. See `missionRows.ts` for the mapping and why a run
 * present in both wins as its live node.
 *
 * Presentation lives in two siblings: `DepartureBoard` (header, headline
 * numbers, table) and `AgentInsight` (one run in full — outcome, story,
 * transcript, review, output). Clicking a row swaps the board for the insight
 * page inside this section; "← Back" swaps it back. Both render with the
 * shared `components/extensions/primitives` so this section looks like Spend,
 * Skills, Plugins, MCPs and CLIs rather than like a screen of its own.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { useSubAgentStore, type SubAgentNode, type SubAgentTreeSnapshot } from "@/store/jarvisAgents";
import { useEventStore } from "@/store/events";
import { DepartureBoard } from "./sub-agents/DepartureBoard";
import { AgentInsight } from "./sub-agents/AgentInsight";
import { selectTaskRows } from "./sub-agents/rows";
import { mergeBoardRows, HISTORY_LIMIT, normalizeTraceId } from "./sub-agents/missionRows";
import { fetchMissions } from "@/components/missions/api";
import { useMissionWebSocket } from "@/components/missions/useMissionWebSocket";
import { useMissionsStore } from "@/components/missions/store";
import { useOutputsList } from "@/hooks/useOutputs";
import { useSectionHealth } from "@/hooks/useProviders";

/** The query parameter the Artifacts section reads to pre-select a run. */
const RUN_PARAM = "run";

export function JarvisAgentsView() {
  const { health } = useSectionHealth();
  const subAgents = useSubAgentStore((s) => s.subAgents);
  const sweepExpired = useSubAgentStore((s) => s.sweepExpired);
  const hydrateSnapshot = useSubAgentStore((s) => s.hydrateSnapshot);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);
  // The run whose insight page is open, by dash-stripped id. Kept as an id
  // rather than a node so the page follows the row through live updates.
  const [openTraceId, setOpenTraceId] = useState<string | null>(null);

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

  // The archive half — optional enrichment, never a blocker: a row whose
  // directory is gone keeps what the mission record says about it.
  const outputsQuery = useOutputsList();

  const nodesList = useMemo(
    () =>
      mergeBoardRows(
        liveRows,
        historyQuery.data?.missions ?? [],
        HISTORY_LIMIT,
        outputsQuery.data ?? [],
      ),
    [liveRows, historyQuery.data, outputsQuery.data],
  );

  const openAgent = useMemo(
    () =>
      openTraceId
        ? nodesList.find((n) => normalizeTraceId(n.trace_id) === openTraceId) ?? null
        : null,
    [nodesList, openTraceId],
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

  // Hand a run to the Artifacts section. That view reads `?run=<slug>` once at
  // mount (VisualizationView), so the address is written first and the section
  // switched second; `useSectionUrlMemory` carries the parameter along.
  const openOutput = useCallback(
    (slug: string) => {
      try {
        const url = new URL(window.location.href);
        url.searchParams.set(RUN_PARAM, slug);
        window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
      } catch {
        /* a WebView that refuses the write still switches sections; the
           Artifacts view then opens on its newest run instead of this one */
      }
      setActiveSection("visualization");
    },
    [setActiveSection],
  );

  const onOpen = useCallback((agent: SubAgentNode) => {
    setOpenTraceId(normalizeTraceId(agent.trace_id));
  }, []);

  if (openAgent) {
    return (
      <AgentInsight
        agent={openAgent}
        onBack={() => setOpenTraceId(null)}
        onOpenOutput={openOutput}
      />
    );
  }

  return (
    <DepartureBoard
      agents={nodesList}
      snapshotError={snapshotError}
      health={health["subagents"] ?? null}
      historyError={historyQuery.isError}
      onOpen={onOpen}
    />
  );
}
