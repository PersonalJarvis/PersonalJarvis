import { useEffect, useRef, useState, type FormEvent } from "react";
import { request, teamPath } from "./api";
import { useSwarmText } from "./strings";
import { exactCount, isTeamUnavailable, type TeamListItem, type TeamRecord } from "./types";

interface Backup { backup_id: string; created_at: number; size_bytes: string; download_url: string }
interface PendingRestore { id: string; team_id: string; phase: string; created_at: number }
interface StorageStatus { backups: Backup[]; pending_restores?: PendingRestore[] }
interface RestoreResult { team: TeamRecord; restore_id: string; quarantine_id: string | null }
interface RetentionResult { expired_messages: string; orphan_objects: string; expired_backups: string; quarantined_workspaces?: string }
interface Props {
  team?: TeamListItem;
  teamId?: string;
  onRestored: (team: TeamRecord) => void;
  onChanged: () => void;
  onDeleted: (id: string) => void;
}
const jsonPost = (body: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** A portable backup link must stay in the selected team's download namespace. */
export function backupLink(teamId: string, backup: Backup): string | undefined {
  const expected = `${teamPath(teamId)}/backups/${encodeURIComponent(backup.backup_id)}`;
  return backup.download_url === expected ? expected : undefined;
}
function storageStatus(value: StorageStatus, teamId?: string): StorageStatus {
  if (!value || !Array.isArray(value.backups) || value.backups.length > 100 || value.backups.some(backup => !backup || !teamId || typeof backup.backup_id !== "string" || !backupLink(teamId, backup) || !Number.isFinite(backup.created_at) || typeof backup.size_bytes !== "string" || !/^\d{1,31}$/.test(backup.size_bytes))) throw new Error("Invalid or mismatched Swarm storage response");
  if (value.pending_restores !== undefined && (!Array.isArray(value.pending_restores) || value.pending_restores.length > 100 || value.pending_restores.some(row => !row || (teamId && row.team_id !== teamId) || typeof row.team_id !== "string" || !row.team_id || typeof row.id !== "string" || !row.id || !Number.isFinite(row.created_at)))) throw new Error("Invalid or mismatched Swarm recovery response");
  return value;
}

export function SwarmStorage({ team, teamId = team?.id, onRestored, onChanged, onDeleted }: Props) {
  const t = useSwarmText();
  const [expanded, setExpanded] = useState(!teamId || isTeamUnavailable(team));
  const [status, setStatus] = useState<StorageStatus | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [replace, setReplace] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteConfirmed, setDeleteConfirmed] = useState(false);
  const [retention, setRetention] = useState<RetentionResult | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const backupKey = useRef<string | null>(null);
  const restoreKey = useRef<string | null>(null);
  const deleteBody = useRef<Record<string, unknown> | null>(null);
  const busyRef = useRef(false);

  useEffect(() => {
    if (!expanded) return;
    const abort = new AbortController();
    setLoading(true); setLoadError("");
    request<StorageStatus>(teamId ? `${teamPath(teamId)}/storage` : "/api/swarm/restores", { signal: abort.signal })
      .then(value => { if (!abort.signal.aborted) setStatus(storageStatus(value, teamId)); })
      .catch(failure => { if (!abort.signal.aborted) setLoadError(String(failure)); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [teamId, expanded, refresh]);

  function changed() { setRefresh(n => n + 1); onChanged(); }
  async function operate(action: string, operation: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(action); setError(""); setNotice("");
    try { await operation(); }
    catch (failure) { setError(String(failure)); }
    finally { busyRef.current = false; setBusy(""); }
  }
  async function createBackup() {
    if (!teamId) return;
    await operate("backup", async () => {
      backupKey.current ??= crypto.randomUUID();
      const backup = await request<Backup>(`${teamPath(teamId)}/backup`, jsonPost({ request_key: backupKey.current }));
      storageStatus({ backups: [backup] }, teamId);
      backupKey.current = null;
      setStatus(previous => ({ ...previous, backups: [backup, ...(previous?.backups ?? []).filter(row => row.backup_id !== backup.backup_id)] }));
      setNotice(t("backupSaved")); changed();
    });
  }
  function restored(result: RestoreResult) {
    restoreKey.current = null; setFile(null); setReplace(false);
    if (fileInput.current) fileInput.current.value = "";
    setNotice(t("backupRestored")); onRestored(result.team); changed();
  }
  async function restore(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    await operate("restore", async () => {
      restoreKey.current ??= crypto.randomUUID();
      const form = new FormData();
      form.append("file", file); form.append("request_key", restoreKey.current);
      if (replace && teamId) form.append("replace_team_id", teamId);
      restored(await request<RestoreResult>("/api/swarm/restores", { method: "POST", body: form }));
    });
  }
  async function removeTeam() {
    if (!teamId || !deleteConfirmed) return;
    await operate("delete", async () => {
      deleteBody.current ??= { confirm_team_id: teamId, request_key: crypto.randomUUID(), ...(!isTeamUnavailable(team) && team ? { expected_version: team.version, expected_storage_generation: team.storage_generation ?? "" } : {}) };
      await request(`${teamPath(teamId)}`, { ...jsonPost(deleteBody.current), method: "DELETE" });
      onDeleted(teamId);
    });
  }
  return <details className="swarm-storage" open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary>{t("storage")}</summary>
    {teamId && <>
      <p className="swarm-muted">{t("retryHint")}</p>
      <div className="swarm-actions">
        <button disabled={Boolean(busy)} onClick={() => void operate("recover", async () => { await request(`${teamPath(teamId)}/recover`, { method: "POST" }); setNotice(t("recovered")); changed(); })}>{t("recover")}</button>
        <button disabled={Boolean(busy) || isTeamUnavailable(team)} onClick={() => void createBackup()}>{t("backup")}</button>
        <button disabled={Boolean(busy) || loading} onClick={changed}>{t("refresh")}</button>
      </div>
      {status && <div className="swarm-storage-backups"><h3>{t("backups")}</h3>
        {status.backups.length === 0 && <p className="swarm-muted">{t("noBackups")}</p>}
        {status.backups.map(backup => <div className="swarm-row" key={backup.backup_id}>
          <span className="swarm-muted">{new Date(backup.created_at * 1000).toLocaleString()} · {exactCount(backup.size_bytes)} {t("bytes")}</span>
          {backupLink(teamId, backup) && <a href={backupLink(teamId, backup)} download>{t("download")}</a>}
        </div>)}
      </div>}
    </>}
    {loading && <p role="status" className="swarm-muted">{t("loading")}</p>}
    {loadError && <p role="alert" className="swarm-error">{loadError}</p>}
    {status?.pending_restores?.map(pending => <div className="swarm-row" key={pending.id}>
      <span className="swarm-muted">{t("restoreInterrupted")} · {new Date(pending.created_at * 1000).toLocaleString()}</span>
      <button disabled={Boolean(busy)} onClick={() => void operate("restore", async () => restored(await request<RestoreResult>(`/api/swarm/restores/${encodeURIComponent(pending.id)}/resume`, { method: "POST" })))}>{t("resumeRestore")}</button>
    </div>)}
    <form aria-label={t("restoreBackup")} className="swarm-storage-form" onSubmit={event => void restore(event)}>
      <h3>{t("restoreBackup")}</h3><p className="swarm-muted">{t("restoreHint")}</p>
      <label>{t("backupFile")}<input ref={fileInput} type="file" accept=".zip,application/zip" required disabled={Boolean(busy)} onChange={event => { setFile(event.target.files?.[0] ?? null); restoreKey.current = null; }} /></label>
      {teamId && <label className="swarm-check"><input type="checkbox" checked={replace} disabled={Boolean(busy)} onChange={event => { setReplace(event.target.checked); restoreKey.current = null; }} /><span>{t("replaceTeam")} <strong>{team?.name ?? teamId}</strong></span></label>}
      {replace && <p className="swarm-notice">{t("replaceTeamHint")}</p>}
      <div className="swarm-actions"><button disabled={!file || Boolean(busy)} type="submit">{t(busy === "restore" ? "restoringBackup" : "restoreBackup")}</button></div>
    </form>
    {teamId && <>
      <div className="swarm-storage-retention"><h3>{t("retention")}</h3><p className="swarm-muted">{t("retentionHint")}</p>
        <button disabled={Boolean(busy) || isTeamUnavailable(team)} onClick={() => void operate("retention", async () => { setRetention(await request<RetentionResult>(`${teamPath(teamId)}/retention`, jsonPost({ before_days: 30 }))); changed(); })}>{t("applyRetention")}</button>
        {retention && <p role="status" className="swarm-notice">{t("expiredMessages")}: {exactCount(retention.expired_messages)} · {t("orphanObjects")}: {exactCount(retention.orphan_objects)} · {t("expiredBackups")}: {exactCount(retention.expired_backups)}{retention.quarantined_workspaces !== undefined && ` · ${t("quarantinedWorkspaces")}: ${exactCount(retention.quarantined_workspaces)}`}</p>}
      </div>
      <div className="swarm-storage-delete"><h3>{t("deleteTeam")}</h3><p className="swarm-muted">{t("deleteHint")}</p>
        {team && !isTeamUnavailable(team) && team.mode === "distributed" && <p className="swarm-muted">{t("distributedDeleteHint")}</p>}
        {confirmingDelete ? <div role="group" aria-label={t("confirmDelete")}>
          <label className="swarm-check"><input type="checkbox" checked={deleteConfirmed} disabled={Boolean(busy)} onChange={event => setDeleteConfirmed(event.target.checked)} /><span>{t("deleteConfirmation")} <strong>{team?.name ?? teamId}</strong></span></label>
          <div className="swarm-actions"><button className="swarm-danger" disabled={!deleteConfirmed || Boolean(busy)} onClick={() => void removeTeam()}>{t("confirmDelete")}</button><button disabled={Boolean(busy)} onClick={() => { setConfirmingDelete(false); setDeleteConfirmed(false); deleteBody.current = null; }}>{t("cancel")}</button></div>
        </div> : <button className="swarm-danger" disabled={Boolean(busy)} onClick={() => setConfirmingDelete(true)}>{t("deleteTeam")}</button>}
      </div>
    </>}
    {error && <p role="alert" className="swarm-error">{error}</p>}
    {notice && <p role="status" className="swarm-notice">{notice}</p>}
  </details>;
}
