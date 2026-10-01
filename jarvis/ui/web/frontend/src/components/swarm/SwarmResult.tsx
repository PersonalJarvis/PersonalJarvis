import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { record, records, request, teamPath } from "./api";
import { useSwarmText } from "./strings";
import type { RecordKind, ScopedRecord, TeamRecord } from "./types";

const DELIVERY = "__swarm_delivery";
const FILE_LIMIT = 8;
const METADATA_LIMIT = 200;
const PREVIEW_BYTES = 100000;
const RESULT_CHARACTERS = 32000;
type ResultFile = { id: string; name: string; mediaType: string; origin: string; taskId: string };
type ResultData = { scope: string; task: ScopedRecord | null; files: ResultFile[]; report?: ResultFile; limited: boolean };
type Preview = { scope: string; file: ResultFile; content: string; truncated: boolean };

function fileRecord(row: ScopedRecord, accepted: Set<string>): ResultFile | null {
  const source = row.provenance as Record<string, unknown> | undefined;
  if (typeof row.id !== "string" || !accepted.has(row.id) || typeof row.name !== "string" || typeof row.task_id !== "string") return null;
  // Runtime observations remain available in the inspector, not in the user's files.
  if (source?.origin !== "worker-authored" && source?.origin !== "worker-output") return null;
  return { id: row.id, name: row.name, taskId: row.task_id, origin: source.origin, mediaType: typeof row.media_type === "string" ? row.media_type : "" };
}
function canPreview(file: ResultFile): boolean {
  return file.mediaType.startsWith("text/") || /^application\/(?:json|[\w.+-]+\+json|xml|[\w.+-]+\+xml|javascript)(?:;|$)/i.test(file.mediaType);
}
const fileUrl = (teamId: string, id: string) => `${teamPath(teamId)}/artifacts/${encodeURIComponent(id)}`;

/** Final output is read from full records; live projections truncate result text. */
export function SwarmResult({ team, awake, onInspect }: {
  team: TeamRecord; awake: boolean; onInspect: (kind: RecordKind, id: string) => void;
}) {
  const t = useSwarmText();
  const eligible = team.state === "succeeded" || team.state === "archived";
  const generation = team.storage_generation ?? "";
  const scope = `${team.id}:${generation}:${team.version}`;
  const [data, setData] = useState<ResultData | null>(null);
  const [error, setError] = useState<{ scope: string; text: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewing, setPreviewing] = useState("");
  const previewAbort = useRef<AbortController | null>(null);
  const current = data?.scope === scope ? data : null;
  const visiblePreview = preview?.scope === scope ? preview : null;

  useEffect(() => {
    previewAbort.current?.abort(); setPreview(null); setPreviewing("");
    if (!eligible || !awake) return;
    const abort = new AbortController();
    setLoading(true); setError(null);
    async function load() {
      try {
        const saved = await request<TeamRecord>(teamPath(team.id), { signal: abort.signal });
        if (saved.id !== team.id || (saved.storage_generation ?? "") !== generation || saved.version !== team.version) throw new Error(t("resultChanged"));
        // Explicit-plan legacy teams do not necessarily have a reserved delivery task.
        const present = Boolean(saved.checkpoint.autonomy) || (await records(team.id, "tasks", 0, abort.signal)).some(row => row.id === DELIVERY);
        const task = present ? await record(team.id, "tasks", DELIVERY, abort.signal) : null;
        if (task && (typeof task.result !== "string" || !Array.isArray(task.evidence) || task.evidence.length > 32 || !task.evidence.every(id => typeof id === "string"))) throw new Error("Invalid final result record");
        const accepted = new Set<string>(task?.state === "succeeded" ? task.evidence as string[] : []);
        const found = new Set<string>(); const files: ResultFile[] = [];
        let report: ResultFile | undefined;
        for (let offset = 0; accepted.size && offset < METADATA_LIMIT; offset += 50) {
          const rows = await records(team.id, "artifacts", offset, abort.signal);
          for (const row of rows) {
            if (typeof row.id === "string" && accepted.has(row.id)) found.add(row.id);
            const file = fileRecord(row, accepted);
            if (!file) continue;
            if (file.origin === "worker-authored") files.push(file);
            else if (file.taskId === DELIVERY && !report) report = file;
          }
          if (rows.length < 50 || found.size === accepted.size) break;
        }
        const latest = await request<TeamRecord>(teamPath(team.id), { signal: abort.signal });
        if (latest.id !== team.id || (latest.storage_generation ?? "") !== generation || latest.version !== team.version) throw new Error(t("resultChanged"));
        if (!abort.signal.aborted) setData({ scope, task: task?.state === "succeeded" ? task : null, files: files.slice(0, FILE_LIMIT), report, limited: files.length > FILE_LIMIT || found.size < accepted.size });
      } catch (failure) {
        if (!abort.signal.aborted) { setData(null); setError({ scope, text: String(failure) }); }
      } finally { if (!abort.signal.aborted) setLoading(false); }
    }
    void load();
    return () => { abort.abort(); previewAbort.current?.abort(); };
  }, [team.id, generation, team.version, scope, eligible, awake, refresh]);

  async function open(file: ResultFile) {
    previewAbort.current?.abort();
    const abort = new AbortController(); previewAbort.current = abort;
    setPreview(null); setPreviewing(file.id); setError(null);
    try {
      const response = await fetch(fileUrl(team.id, file.id), { signal: abort.signal });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const reader = response.body?.getReader();
      if (!reader) throw new Error(t("resultPreviewUnavailable"));
      const decoder = new TextDecoder(); let content = ""; let bytes = 0; let done = false;
      try {
        while (bytes < PREVIEW_BYTES) {
          const chunk = await reader.read();
          if (chunk.done) { done = true; break; }
          const take = chunk.value.subarray(0, PREVIEW_BYTES - bytes);
          bytes += take.byteLength; content += decoder.decode(take, { stream: true });
        }
        content += decoder.decode();
      } finally { await reader.cancel(); reader.releaseLock(); }
      const latest = await request<TeamRecord>(teamPath(team.id), { signal: abort.signal });
      if (latest.id !== team.id || (latest.storage_generation ?? "") !== generation || latest.version !== team.version) {
        if (!abort.signal.aborted) setData(null);
        throw new Error(t("resultChanged"));
      }
      if (!abort.signal.aborted) setPreview({ scope, file, content, truncated: !done });
    } catch (failure) { if (!abort.signal.aborted) setError({ scope, text: String(failure) }); }
    finally { if (!abort.signal.aborted) setPreviewing(""); }
  }

  if (!eligible) return null;
  return <section className="swarm-panel swarm-result" aria-label={t("yourResult")} aria-busy={loading}>
    <div className="swarm-row"><h2>{t("yourResult")}</h2><button disabled={loading} onClick={() => setRefresh(value => value + 1)}>{t("refresh")}</button></div>
    {!current && loading && <p className="swarm-muted">{t("loading")}</p>}
    {error?.scope === scope && <p className="swarm-error" role="alert">{error.text}</p>}
    {current?.task && <>
      <p className="swarm-muted">{t("resultAccepted")}</p>
      <div className="swarm-result-text"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        img: ({ alt }) => <span>{alt}</span>,
        a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
        code: ({ children, className }) => <code className={className}>{
          current.files.find(file => file.id === String(children))?.name ?? children
        }</code>,
      }}>{String(current.task.result).slice(0, RESULT_CHARACTERS)}</ReactMarkdown></div>
      {String(current.task.result).length > RESULT_CHARACTERS && <p className="swarm-muted">{t("resultSummaryTruncated")}</p>}
      {current.report && <a className="swarm-result-download" href={fileUrl(team.id, current.report.id)} download={current.report.name}>{t("downloadResult")}</a>}
      {current.files.length > 0 && <div><h3>{t("resultFiles")}</h3><ul className="swarm-result-files">{current.files.map(file => <li key={file.id}>
        <strong>{file.name}</strong><div className="swarm-actions">
          {canPreview(file) && <button disabled={previewing === file.id} onClick={() => void open(file)}>{t("previewFile")}</button>}
          <a href={fileUrl(team.id, file.id)} download={file.name}>{t("download")}</a>
        </div>
      </li>)}</ul></div>}
      {current.limited && <p className="swarm-muted">{t("resultFilesLimited")}</p>}
    </>}
    {current && !current.task && <p className="swarm-muted">{t("resultUnavailable")}</p>}
    {visiblePreview && <div className="swarm-result-preview"><div className="swarm-row"><h3>{visiblePreview.file.name}</h3><button onClick={() => setPreview(null)}>{t("closePreview")}</button></div><pre>{visiblePreview.content}</pre>{visiblePreview.truncated && <p className="swarm-muted">{t("previewTruncated")}</p>}</div>}
    <div className="swarm-actions">
      <button onClick={() => onInspect("tasks", current?.task ? DELIVERY : "")}>{t("inspectCompletedWork")}</button>
      <button onClick={() => onInspect("artifacts", "")}>{t("inspectAllFiles")}</button>
    </div>
  </section>;
}
