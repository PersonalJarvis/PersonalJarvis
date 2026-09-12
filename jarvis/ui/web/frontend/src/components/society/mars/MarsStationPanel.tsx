import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocaleChunk, useT } from "@/i18n";
import { containsCredential } from "./credentialInput";
import {
  WORLD_ID, cancelMarsCommand, fetchMarsRoster, fetchMarsSnapshot, submitMarsDraft,
  readDraftAttempt, saveDraftAttempt, clearDraftAttempt, MarsApiError,
  type DraftAttempt, type MarsCommand,
} from "./api";

const SNAPSHOT_KEY = ["mars", WORLD_ID, "snapshot"];

/** Operator control remains available independently of player travel or graphics. */
export function MarsStationPanel({ onClose, onOpenAgent }: {
  onClose: () => void; onOpenAgent?: (id: string) => void;
}) {
  const t = useT();
  const ready = useLocaleChunk("society");
  const client = useQueryClient();
  const attempt = useRef<DraftAttempt | null>(readDraftAttempt());
  const [agentId, setAgentId] = useState(attempt.current?.agent_id ?? "");
  const [draft, setDraft] = useState(attempt.current?.draft ?? "");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [credentialInput, setCredentialInput] = useState(false);
  const snapshot = useQuery({
    queryKey: SNAPSHOT_KEY,
    queryFn: ({ signal }) => fetchMarsSnapshot(signal),
    refetchInterval: 2500, retry: false,
  });
  const roster = useQuery({
    queryKey: ["mars", WORLD_ID, "roster"],
    queryFn: ({ signal }) => fetchMarsRoster(signal),
    retry: false,
  });
  const activeAgents = (roster.data ?? []).filter((agent) => agent.state === "active");
  const selectedAgent = agentId || activeAgents[0]?.agent_id || "";
  useEffect(() => {
    const pending = attempt.current;
    if (!pending || busy || snapshot.isError) return;
    const recorded = snapshot.data?.commands.some(
      (command) => command.request_id === pending.request_id && command.agent_id === pending.agent_id,
    );
    if (recorded) {
      clearDraftAttempt(pending.request_id);
      attempt.current = null; setDraft(""); setFailed(false);
    }
  }, [snapshot.data, snapshot.isError, busy]);
  const submit = async () => {
    if (busy || (!attempt.current && (!selectedAgent || !draft.trim()))) return;
    if (containsCredential(attempt.current?.draft ?? draft)) {
      if (attempt.current) clearDraftAttempt(attempt.current.request_id);
      attempt.current = null; setDraft(""); setCredentialInput(true); setFailed(true);
      return;
    }
    if (!attempt.current) {
      attempt.current = { request_id: crypto.randomUUID(), agent_id: selectedAgent, draft: draft.trim() };
      saveDraftAttempt(attempt.current);
    }
    const submitted = attempt.current;
    setBusy(true); setFailed(false); setCredentialInput(false);
    try {
      await submitMarsDraft(submitted);
      clearDraftAttempt(submitted.request_id);
      if (attempt.current?.request_id === submitted.request_id) { attempt.current = null; setDraft(""); }
      await client.invalidateQueries({ queryKey: SNAPSHOT_KEY });
    } catch (error) {
      // Confirmed validation/access failures committed no task. Uncertain delivery keeps its ID.
      if (error instanceof MarsApiError && [400, 401, 403, 404, 422].includes(error.status)) {
        clearDraftAttempt(submitted.request_id);
        if (attempt.current?.request_id === submitted.request_id) attempt.current = null;
        setCredentialInput(error.credentialInput);
        if (error.credentialInput) setDraft("");
      }
      setFailed(true);
    } finally { setBusy(false); }
  };
  const cancel = async (command: MarsCommand) => {
    if (busy) return;
    setBusy(true); setFailed(false);
    try {
      await cancelMarsCommand(command);
      await client.invalidateQueries({ queryKey: SNAPSHOT_KEY });
    } catch { setFailed(true); }
    finally { setBusy(false); }
  };
  if (!ready) return null;
  return (
    <aside
      aria-label={t("society.mars.station_title")} data-mars-ui data-mars-station
      className="flex w-full flex-col gap-3 rounded-xl border border-border bg-popover p-4 text-foreground shadow-float"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-semibold">{t("society.mars.station_title")}</h2>
        <button type="button" onClick={onClose} className="rounded px-2 py-1 hover:bg-accent">{t("society.mars.close")}</button>
      </div>
      <p className="text-sm text-muted-foreground">{t("society.mars.station_scope")}</p>
      <label className="grid gap-1 text-sm">
        {t("society.mars.agent")}
        <select value={selectedAgent} disabled={busy || !!attempt.current} onChange={(event) => setAgentId(event.target.value)} className="rounded border border-border bg-background p-2">
          {activeAgents.map((agent) => <option key={agent.agent_id} value={agent.agent_id}>{agent.name}</option>)}
        </select>
      </label>
      {!activeAgents.length && <p role="status" className="text-sm">{t(roster.isError ? "society.mars.offline" : "society.mars.no_agents")}</p>}
      <label className="grid gap-1 text-sm">
        {t("society.mars.draft")}
        <textarea value={draft} maxLength={4000} rows={4} disabled={busy || !!attempt.current} onChange={(event) => setDraft(event.target.value)} className="resize-y rounded border border-border bg-background p-2" />
      </label>
      <button type="button" disabled={busy || (!attempt.current && (!selectedAgent || !draft.trim()))} onClick={() => void submit()} className="rounded border border-border bg-primary px-3 py-2 text-primary-foreground disabled:opacity-50">
        {t(attempt.current ? "society.mars.retry_same" : "society.mars.prepare_draft")}
      </button>
      {failed && <p role="alert" className="text-sm">{t(credentialInput ? "society.mars.credential_input" : attempt.current ? "society.mars.uncertain" : "society.mars.request_failed")}</p>}
      <h3 className="text-sm font-semibold">{t("society.mars.recorded_work")}</h3>
      {snapshot.isError && <p role="status" className="text-sm">{t("society.mars.offline")}</p>}
      {!snapshot.isError && (snapshot.data?.commands ?? []).slice(0, 12).map((command) => (
        <article key={command.command_id} className="rounded border border-border bg-background p-2 text-sm">
          <p>{activeAgents.find((agent) => agent.agent_id === command.agent_id)?.name ?? command.agent_id} · {t(`society.mars.state_${command.state}`)}</p>
          <p className="break-all font-mono text-[10px] text-muted-foreground">{command.command_id}</p>
          {command.reason && <p className="text-xs">{t("society.mars.inspect_reason")}</p>}
          <div className="mt-2 flex flex-wrap gap-2">
            {onOpenAgent && <button type="button" onClick={() => onOpenAgent(command.agent_id)} className="rounded border border-border px-2 py-1">{t("society.mars.open_conversation")}</button>}
            {!["completed", "failed", "canceled"].includes(command.state) && <button type="button" disabled={busy || command.cancel_requested} onClick={() => void cancel(command)} className="rounded border border-border px-2 py-1 disabled:opacity-50">{t(command.cancel_requested ? "society.mars.stopping" : "society.mars.stop")}</button>}
          </div>
        </article>
      ))}
    </aside>
  );
}
