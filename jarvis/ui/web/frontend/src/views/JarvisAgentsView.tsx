/**
 * SubAgentsView — Live-Tafel aller aktiven Jarvis-Agents.
 *
 * Statt einer ReactFlow-Canvas mit Karten rendert die View jetzt eine
 * Bahnhofs-Abfahrtstafel (DepartureBoard), die sich kontinuierlich vom
 * leeren Standby-Zustand zum aktiven Drilldown-Zustand fuellt. Alle
 * Tool-Calls sind inline pro Agent-Zeile aufklappbar — kein Box-Layout,
 * keine Canvas, alles in einer Spalte.
 */
import { Trash2, Users } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useSubAgentStore, type SubAgentTreeSnapshot } from "@/store/jarvisAgents";
import { DepartureBoard } from "./sub-agents/DepartureBoard";
import { selectTaskRows } from "./sub-agents/rows";
import { useMissionWebSocket } from "@/components/missions/useMissionWebSocket";
import { useMissionsStore } from "@/components/missions/store";
import { useT } from "@/i18n";

export function JarvisAgentsView() {
  const t = useT();
  const subAgents = useSubAgentStore((s) => s.subAgents);
  const sweepExpired = useSubAgentStore((s) => s.sweepExpired);
  const hydrateSnapshot = useSubAgentStore((s) => s.hydrateSnapshot);
  const clear = useSubAgentStore((s) => s.clear);
  const [snapshotError, setSnapshotError] = useState<string | null>(null);

  // One row per task: collapse each worker into its mission row so a single
  // dispatched task shows once (the mission "Sub-Agent" carrying the task
  // text), not twice (mission + its "Worker" child). The store keeps both
  // nodes for the DetailPanel; this only filters the displayed list. Header
  // counts and the DepartureBoard metric panel both derive from this array,
  // so they stay consistent with the rows actually shown.
  const nodesList = useMemo(() => selectTaskRows(subAgents), [subAgents]);

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

  const activeCount = nodesList.filter((n) => n.status === "running").length;

  return (
    <div className="relative h-full w-full flex flex-col bg-zinc-950">
      <header className="px-4 py-3 border-b border-zinc-800 flex items-center justify-between shrink-0 z-10 bg-zinc-950/80 backdrop-blur">
        <div className="flex items-center gap-3">
          <Users className="h-5 w-5 text-sky-400" />
          <div>
            <div className="text-sm font-semibold text-zinc-100">{t("subagents_view.title")}</div>
            <div className="text-[11px] text-zinc-500">
              {activeCount} {t("subagents_view.stat_active")} · {nodesList.length} {t("subagents_view.stat_total")}
            </div>
          </div>
        </div>
        <button
          onClick={() => clear()}
          className="text-xs px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 flex items-center gap-1.5"
          title={t("subagents_view.clear_tooltip")}
        >
          <Trash2 className="h-3 w-3" />
          {t("subagents_view.clear")}
        </button>
      </header>

      <div className="flex-1 relative overflow-hidden">
        <DepartureBoard agents={nodesList} snapshotError={snapshotError} />
      </div>
    </div>
  );
}
