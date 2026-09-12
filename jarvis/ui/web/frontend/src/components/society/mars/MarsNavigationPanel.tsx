import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useT } from "@/i18n";
import { MarsApiError } from "./api";
import { cancelMove, clearMoveAttempt, latestNavigationRecords, readMoveAttempt, saveMoveAttempt, submitMove, type MoveAttempt } from "./navigationApi";
import { NAVIGATION_KEY, useMarsNavigation } from "./useMarsNavigation";

export const VISIT_DESTINATIONS = ["communications-console", "outpost-bridge-staging", "outpost-approach"] as const;

/** Moving and stopping here changes physical location, never the agent's real task. */
export function MarsNavigationPanel({ agentId }: { agentId: string }) {
  const t = useT();
  const query = useMarsNavigation();
  const client = useQueryClient();
  const pending = useRef<MoveAttempt | null>(readMoveAttempt());
  const [destination, setDestination] = useState(pending.current?.station_id ?? "communications-console");
  const [busy, setBusy] = useState(false), [failed, setFailed] = useState(false);
  const record = latestNavigationRecords(query.data).find((row) => row.agent_id === agentId);
  const inMotion = record && !["arrived", "unreachable", "canceled"].includes(record.state);
  useEffect(() => {
    const attempt = pending.current;
    if (!attempt || busy || query.isError) return;
    if (query.data?.commands.some((row) => row.agent_id === attempt.agent_id && row.request_id === attempt.request_id)) {
      clearMoveAttempt(attempt.request_id); pending.current = null; setFailed(false);
    }
  }, [query.data, query.isError, busy]);
  const move = async (target = destination) => {
    if (busy || !agentId) return;
    const attempt = pending.current ?? { request_id: crypto.randomUUID(), agent_id: agentId, station_id: target };
    pending.current = attempt; saveMoveAttempt(attempt); setBusy(true); setFailed(false);
    try {
      await submitMove(attempt);
      clearMoveAttempt(attempt.request_id);
      if (pending.current?.request_id === attempt.request_id) pending.current = null;
      await client.invalidateQueries({ queryKey: NAVIGATION_KEY });
    } catch (error) {
      if (error instanceof MarsApiError && [400, 401, 403, 404, 409, 422].includes(error.status)) {
        clearMoveAttempt(attempt.request_id); pending.current = null;
      }
      setFailed(true);
    } finally { setBusy(false); }
  };
  const stop = async () => {
    if (!record || busy) return;
    setBusy(true); setFailed(false);
    try { await cancelMove(record); await client.invalidateQueries({ queryKey: NAVIGATION_KEY }); }
    catch { setFailed(true); }
    finally { setBusy(false); }
  };
  return <section className="grid gap-2 border-t border-border pt-3" aria-label={t("society.mars.visits")} data-mars-ui>
    <h3 className="text-sm font-semibold">{t("society.mars.visits")}</h3>
    <p className="text-xs text-muted-foreground">{t("society.mars.visit_scope")}</p>
    {pending.current && <p className="text-xs text-muted-foreground">{t("society.mars.pending_visit")}: {pending.current.agent_id} · {t(`society.mars.destination_${pending.current.station_id}`)}</p>}
    <label className="grid gap-1 text-sm">{t("society.mars.destination")}
      <select value={destination} disabled={busy || !!pending.current} onChange={(event) => setDestination(event.target.value)} className="rounded border border-border bg-background p-2">
        {VISIT_DESTINATIONS.map((id) => <option key={id} value={id}>{t(`society.mars.destination_${id}`)}</option>)}
      </select>
    </label>
    {destination === "outpost-approach" && <p className="text-xs text-muted-foreground">{t("society.mars.approach_hint")}</p>}
    <div className="flex flex-wrap gap-2 text-sm">
      <button type="button" disabled={busy || !agentId || (!pending.current && !!inMotion)} onClick={() => void move()} className="rounded border border-border px-2 py-1 disabled:opacity-50">{t(pending.current ? "society.mars.retry_visit" : "society.mars.start_visit")}</button>
      {inMotion && <button type="button" disabled={busy} onClick={() => void stop()} className="rounded border border-border px-2 py-1 disabled:opacity-50">{t("society.mars.stop_visit")}</button>}
      {record?.presence === "placed" && !inMotion && record.station_id !== "outpost-bridge-staging" && <button type="button" disabled={busy || !!pending.current} onClick={() => void move("outpost-bridge-staging")} className="rounded border border-border px-2 py-1 disabled:opacity-50">{t("society.mars.park_by_bridge")}</button>}
    </div>
    {record && <p role="status" className="text-sm">{t(`society.mars.move_state_${record.state}`)}{record.presence === "spawn_queue" ? ` · ${t("society.mars.waiting_entry")}` : ""}</p>}
    {record?.state === "canceled" && record.presence === "placed" && <p className="text-xs text-muted-foreground">{t("society.mars.stopped_in_place")}</p>}
    {query.isError && <p role="status" className="text-sm">{t("society.mars.positions_offline")}</p>}
    {failed && <p role="alert" className="text-sm">{t(pending.current ? "society.mars.visit_uncertain" : "society.mars.request_failed")}</p>}
  </section>;
}
