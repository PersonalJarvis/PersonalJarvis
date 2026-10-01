import { useEffect, useRef, useState } from "react";
import { BrandedSelect } from "@/components/ui/select";
import { record, records, request, teamPath } from "./api";
import { useSwarmText } from "./strings";
import { SwarmRecheck, SwarmSkills } from "./SwarmSkills";
import { exactCount, type RecordKind, type ScopedRecord, type WorldSnapshot } from "./types";

const KINDS: RecordKind[] = ["agents", "tasks", "messages", "events", "decisions", "checkpoints", "artifacts", "reputation", "publications"];
const TITLES: Record<string, string> = { name: "name", title: "task", summary: "result", state: "filter", reason: "reason", role: "owner", result: "result", acceptance: "acceptance", description: "goal", dependencies: "taskDependencies", evidence: "evidence", owner_id: "owner", milestone: "branch", difficulty: "difficulty", attempt_count: "attempts", level: "level", reliability: "reliability", verified_tasks: "verified", generation: "version", domain: "group", tool_activity: "active", task_id: "task", agent_id: "agents", sender_id: "source", recipients: "recipients", intent: "intent", confidence: "confidence", trace_id: "trace", created_at: "timestamp", version: "version" };
export function recordId(row: ScopedRecord): string { return String(row.id ?? row.agent_id ?? row.event_id ?? row.artifact_id ?? ""); }
function textValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
}

export function SwarmInspector({ snapshot, kind, selected, onSelect, awake }: {
  snapshot: WorldSnapshot; kind: RecordKind; selected: string;
  onSelect: (kind: RecordKind, id: string) => void; awake: boolean;
}) {
  const t = useSwarmText();
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<ScopedRecord[]>([]);
  const [loadedKind, setLoadedKind] = useState<RecordKind>(kind);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [artifact, setArtifact] = useState<{ id: string; content: string } | null>(null);
  const [notice, setNotice] = useState("");
  const [publishing, setPublishing] = useState(false);
  const [publishKeys] = useState(() => new Map<string, string>());
  const [detail, setDetail] = useState<{ kind: RecordKind; row: ScopedRecord } | null>(null);
  const artifactAbort = useRef<AbortController | null>(null);
  const teamId = snapshot.team.id;
  useEffect(() => () => artifactAbort.current?.abort(), []);
  useEffect(() => { setOffset(0); setSearch(""); setStatus(""); setArtifact(null); setNotice(""); }, [kind]);
  useEffect(() => {
    if (!awake) return;
    const abort = new AbortController();
    setLoading(true); setError("");
    records(teamId, kind, offset, abort.signal).then(data => {
      if (!abort.signal.aborted) { setRows(data); setLoadedKind(kind); }
    }).catch(failure => { if (!abort.signal.aborted) setError(String(failure)); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [teamId, kind, offset, refresh, awake]);
  // Current task/agent state comes from the live projection; paginated history stays on demand.
  const liveRows = kind === "agents" ? snapshot.agents : kind === "tasks" ? snapshot.tasks : [];
  const loadedRows = loadedKind === kind ? rows : [];
  const liveById = new Map(liveRows.map(item => [item.id, item as unknown as ScopedRecord]));
  const visibleRows = offset === 0 && liveRows.length > 0
    ? [...liveRows as unknown as ScopedRecord[], ...loadedRows.filter(item => !liveById.has(recordId(item)))].slice(0, 50)
    : loadedRows.map(item => liveById.get(recordId(item)) ?? item);
  const live = liveRows.find(row => row.id === selected) as unknown as ScopedRecord | undefined;
  const full = detail?.kind === kind && detail.row.id === selected && detail.row.team_id === teamId ? detail.row : undefined;
  const selectedVersion = live?.version;
  // The stream deliberately truncates results and evidence. Prefer the full
  // record once it matches the current task version, while agents stay live.
  const row = full && (!live || kind !== "tasks" || full.version === selectedVersion)
    ? { ...full, ...(kind === "agents" ? live : {}) }
    : live ?? visibleRows.find(item => recordId(item) === selected);
  useEffect(() => {
    if (!awake || !selected || (kind !== "agents" && kind !== "tasks" && kind !== "decisions" && kind !== "checkpoints")) return;
    const abort = new AbortController();
    record(teamId, kind, selected, abort.signal).then(data => { if (!abort.signal.aborted) setDetail({ kind, row: data }); })
      .catch(failure => { if (!abort.signal.aborted) setError(String(failure)); });
    return () => abort.abort();
  }, [teamId, kind, selected, selectedVersion, refresh, awake]);
  const filtered = visibleRows.filter(item => (!status || item.state === status) && (!search || textValue(item).toLocaleLowerCase().includes(search.toLocaleLowerCase())));
  async function openArtifact(id: string) {
    setError("");
    artifactAbort.current?.abort();
    const abort = new AbortController(); artifactAbort.current = abort;
    try {
      const response = await fetch(`${teamPath(teamId)}/artifacts/${encodeURIComponent(id)}`, { signal: abort.signal });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const reader = response.body?.getReader();
      if (!reader) throw new Error("Artifact preview is unavailable; use Download");
      const decoder = new TextDecoder(); let content = ""; let bytes = 0;
      try {
        while (bytes < 100000) {
          const chunk = await reader.read();
          if (chunk.done) break;
          const take = chunk.value.subarray(0, 100000 - bytes);
          bytes += take.byteLength; content += decoder.decode(take, { stream: true });
        }
        content += decoder.decode();
      } finally { await reader.cancel(); reader.releaseLock(); }
      if (!abort.signal.aborted) setArtifact({ id, content: content + (bytes >= 100000 ? `\n${t("previewTruncated")}` : "") });
    } catch (failure) { if (!abort.signal.aborted) setError(String(failure)); }
  }
  async function publish(id: string) {
    setPublishing(true); setError(""); setNotice("");
    try {
      let key = publishKeys.get(id);
      if (!key) { key = crypto.randomUUID(); publishKeys.set(id, key); }
      await request(`${teamPath(teamId)}/publish`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ artifact_id: id, destination: "artifact", expected_version: snapshot.team.version, expected_storage_generation: snapshot.team.storage_generation ?? "", request_key: key }) });
      setNotice(t("published")); setRefresh(n => n + 1);
    } catch (failure) { setError(String(failure)); }
    finally { setPublishing(false); }
  }
  return <section className="swarm-panel" aria-label={t("inspect")}>
    <div className="swarm-tabs" aria-label={t("inspect")}>{KINDS.map(tab => <button key={tab} aria-pressed={kind === tab} onClick={() => onSelect(tab, "")}>{t(tab)}</button>)}</div>
    <div className="swarm-row"><label><span className="sr-only">{t("search")}</span><input placeholder={t("search")} value={search} onChange={e => setSearch(e.target.value)} /></label>
      <div className="swarm-actions"><BrandedSelect ariaLabel={t("filter")} value={status} onValueChange={setStatus} className="w-auto min-w-32" options={[
        { value: "", label: t("all") },
        ...[...new Set(visibleRows.map(item => item.state).filter((value): value is string => typeof value === "string"))].map(value => ({ value, label: t(value) })),
      ]} />
        <button disabled={loading} onClick={() => setRefresh(n => n + 1)}>{t("refresh")}</button></div></div>
    {error && <p className="swarm-error" role="alert">{error}</p>}{notice && <p role="status" className="swarm-notice">{notice}</p>}
    {kind === "reputation" && <p className="swarm-muted">{t("uncertainty")}</p>}
    <div className="swarm-inspector" aria-busy={loading}>
      <div className="swarm-record-list">{loading && <p className="swarm-muted">{t("loading")}</p>}{!loading && !filtered.length && <p className="swarm-muted">{t("noRecords")}</p>}
        {filtered.map((item, index) => { const id = recordId(item); const display = liveById.get(id) ?? item;
          return <button className="swarm-record" key={id || index} aria-pressed={selected === id} onClick={() => { onSelect(kind, id); setArtifact(null); }}>
            <strong>{display.kind === "checkpoint_snapshot" ? `${t("checkpoint")} ${exactCount(String(display.version))}` : String(display.name ?? display.title ?? display.summary ?? display.operation ?? display.intent ?? display.kind ?? display.agent_id ?? id)}</strong>
            {typeof display.state === "string" && <span className="swarm-state" data-state={display.state}>{t(display.state)}</span>}
            {typeof display.role === "string" && <span className="swarm-muted">{t(display.role)} · {t("level")} {String(display.level ?? 1)}</span>}
            {typeof display.task_id === "string" && <span className="swarm-muted">{t("task")}: {display.task_id}</span>}
          </button>; })}
      </div>
      <div className="swarm-detail" aria-label={t("details")}>
        {!row && <p className="swarm-muted">{t("inspect")}: {t(kind)}</p>}
        {row && <><h3>{String(row.name ?? row.title ?? row.summary ?? recordId(row))}</h3><dl>
          {Object.entries(row).filter(([key, value]) => value !== null && value !== "" && !["team_id", "id", "name", "title", "token", "authority"].includes(key)).map(([key, value]) => <div key={key}>
            <dt>{t(TITLES[key] ?? key)}</dt><dd>
              {key === "dependencies" && Array.isArray(value) ? value.map(id => <button key={String(id)} className="swarm-dependency" onClick={() => onSelect("tasks", String(id))}>{String(id)}</button>) :
                key === "evidence" && Array.isArray(value) ? value.map(id => <button key={String(id)} className="swarm-dependency" onClick={() => void openArtifact(String(id))}>{String(id)}</button>) :
                  key === "task_id" && typeof value === "string" ? <button onClick={() => onSelect("tasks", value)}>{value}</button> :
                    key === "owner_id" && typeof value === "string" ? <button onClick={() => onSelect("agents", value)}>{value}</button> :
                      key === "created_at" && typeof value === "number" ? new Date(value * 1000).toLocaleString() :
                        key === "verified_tasks" && typeof value === "string" ? exactCount(value) :
                          ["state", "role"].includes(key) && typeof value === "string" ? t(value) : textValue(value)}
            </dd></div>)}
        </dl>
          {kind === "events" && row.data && typeof row.data === "object" && typeof (row.data as Record<string, unknown>).snapshot_id === "string" && <button onClick={() => onSelect("checkpoints", String((row.data as Record<string, unknown>).snapshot_id))}>{t("openCheckpoint")}</button>}
          {kind === "agents" && <SwarmSkills key={`${teamId}:${recordId(row)}`} teamId={teamId} agentId={recordId(row)} awake={awake} onTask={id => onSelect("tasks", id)} />}
          {kind === "tasks" && row.state === "succeeded" && <SwarmRecheck key={`${teamId}:${recordId(row)}`} teamId={teamId} taskId={recordId(row)} onEvidence={id => void openArtifact(id)} />}
          {kind === "artifacts" && <div className="swarm-actions"><button onClick={() => void openArtifact(recordId(row))}>{t("open")}</button><button disabled={publishing} onClick={() => void publish(recordId(row))}>{t("publish")}</button></div>}
          {kind === "publications" && <a href={`/api/swarm/publications/${encodeURIComponent(recordId(row))}`} target="_blank" rel="noopener noreferrer">{t("open")}</a>}
        </>}
        {artifact && <section><h3>{t("content")}: {artifact.id}</h3><pre>{artifact.content}</pre><a href={`${teamPath(teamId)}/artifacts/${encodeURIComponent(artifact.id)}`} download>{t("download")}</a></section>}
      </div>
    </div>
    <div className="swarm-pagination"><button disabled={!offset || loading} onClick={() => setOffset(n => Math.max(0, n - 50))}>{t("previous")}</button><span className="swarm-muted">{t("page")} {offset / 50 + 1}</span><button disabled={visibleRows.length < 50 || loading} onClick={() => setOffset(n => n + 50)}>{t("next")}</button></div>
  </section>;
}
