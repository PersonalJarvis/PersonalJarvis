import { useEffect, useRef, useState, type FormEvent } from "react";
import { BrandedSelect } from "@/components/ui/select";
import { records, request, teamPath } from "./api";
import { useSwarmText } from "./strings";
import { terminalState, type AgentRecord, type TeamRecord } from "./types";

interface SourceProfile { id: string; name: string; title: string; focus: string[] }

/** Read the existing roster endpoint, retaining only selectable public identity fields. */
export function activeSourceProfiles(value: unknown): SourceProfile[] {
  if (!value || typeof value !== "object" || !Array.isArray((value as { agents?: unknown }).agents)) throw new Error("Invalid specialist roster response");
  const rows = (value as { agents: Record<string, unknown>[] }).agents;
  if (rows.some(row => !row || typeof row !== "object")) throw new Error("Invalid specialist roster response");
  return rows.filter(row => row.state === "active" && typeof row.agent_id === "string" && typeof row.name === "string" && typeof row.title === "string" && Array.isArray(row.focus) && row.focus.every(item => typeof item === "string"))
    .map(row => ({ id: row.agent_id as string, name: row.name as string, title: row.title as string, focus: row.focus as string[] }));
}

export function SpecialistAssignments({ team, onChanged }: { team: TeamRecord; onChanged: () => void }) {
  const t = useSwarmText();
  const [open, setOpen] = useState(false);
  return <details className="swarm-panel" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{t("specialistAssignments")}</summary>
    {open && <Assignments key={team.id} team={team} onChanged={onChanged} />}
  </details>;
}

function Assignments({ team, onChanged }: { team: TeamRecord; onChanged: () => void }) {
  const t = useSwarmText();
  const [sources, setSources] = useState<SourceProfile[]>([]);
  const [members, setMembers] = useState<AgentRecord[]>([]);
  const [pageSize, setPageSize] = useState(0);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState("");
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refresh, setRefresh] = useState(0);
  const requestKey = useRef(crypto.randomUUID());
  const lastPayload = useRef("");
  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    Promise.allSettled([
      terminalState(team.state) ? Promise.resolve([]) : request<unknown>("/api/society/agents", { signal: abort.signal }).then(activeSourceProfiles),
      records(team.id, "agents", offset, abort.signal),
    ]).then(([profiles, result]) => {
      if (abort.signal.aborted) return;
      setSources(profiles.status === "fulfilled" ? profiles.value : []);
      const rows = result.status === "fulfilled" ? result.value : [];
      setPageSize(rows.length);
      setMembers(rows.filter(row => typeof row.source_agent_id === "string" && row.role === "worker").map(row => row as unknown as AgentRecord));
      const failures = [profiles, result].filter(result => result.status === "rejected");
      if (failures.length) setError(failures.map(result => String(result.reason)).join(" · "));
    })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [team.id, team.state, offset, refresh]);
  const selectedProfile = sources.find(source => source.id === selected);
  async function assign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !selectedProfile || terminalState(team.state)) return;
    const content = { source_agent_id: selectedProfile.id, authorized_input: input };
    const serialized = JSON.stringify(content);
    if (lastPayload.current && serialized !== lastPayload.current) requestKey.current = crypto.randomUUID();
    lastPayload.current = serialized;
    setBusy("assign"); setError(""); setNotice("");
    try {
      const member = await request<AgentRecord>(`${teamPath(team.id)}/specialists`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...content, request_key: requestKey.current, expected_version: team.version, expected_storage_generation: team.storage_generation ?? "" }) });
      if (member.team_id !== team.id || member.source_agent_id !== selectedProfile.id) throw new Error("Mismatched specialist assignment response");
      setNotice(t("specialistAssigned")); setInput(""); setSelected("");
      requestKey.current = crypto.randomUUID(); lastPayload.current = "";
      setRefresh(n => n + 1); onChanged();
    } catch (failure) { setError(String(failure)); onChanged(); }
    finally { setBusy(""); }
  }
  async function revoke(member: AgentRecord) {
    if (busy) return;
    setBusy(member.id); setError(""); setNotice("");
    try {
      await request(`${teamPath(team.id)}/specialists/${encodeURIComponent(member.id)}`, { method: "DELETE" });
      setNotice(t("specialistRevoked")); setRefresh(n => n + 1); onChanged();
    } catch (failure) { setError(String(failure)); }
    finally { setBusy(""); }
  }
  return <>
    <p className="swarm-muted">{t("assignmentScopeHint")}</p>
    {error && <p className="swarm-error" role="alert">{error}</p>}
    {notice && <p className="swarm-notice" role="status">{notice}</p>}
    <button disabled={loading || Boolean(busy)} onClick={() => { setError(""); setRefresh(n => n + 1); }}>{t("refresh")}</button>
    {loading && <p className="swarm-muted">{t("loading")}</p>}
    {!terminalState(team.state) && <form className="swarm-create" onSubmit={assign} aria-label={t("assignSpecialist")}>
      <label>{t("chooseSpecialist")}<BrandedSelect ariaLabel={t("chooseSpecialist")} value={selected} onValueChange={setSelected} disabled={loading || Boolean(busy)} options={[
        { value: "", label: t("chooseSpecialist") },
        ...sources.map(source => ({ value: source.id, label: `${source.name}${source.title ? ` · ${source.title}` : ""}` })),
      ]} /></label>
      {!loading && !sources.length && <p className="swarm-muted">{t("noActiveSpecialists")}</p>}
      {selectedProfile && <p className="swarm-muted">{selectedProfile.focus.join(" · ")}</p>}
      <label>{t("authorizedInput")}<textarea rows={4} maxLength={16000} value={input} onChange={event => setInput(event.target.value)} disabled={Boolean(busy)} /></label>
      <button className="swarm-primary" disabled={loading || Boolean(busy) || !selectedProfile} type="submit">{t("assignSpecialist")}</button>
    </form>}
    <div className="swarm-record-list">{members.map(member => <article key={member.id} aria-label={member.name}>
      <div className="swarm-row"><strong>{member.name}</strong><span className="swarm-state" data-state={member.state}>{t(member.state)}</span></div>
      <p className="swarm-muted">{t("sourceSpecialist")}: {member.source_agent_id}</p>
      <button disabled={Boolean(busy) || member.state === "stopped"} onClick={() => void revoke(member)}>{t("revokeSpecialist")}</button>
    </article>)}</div>
    {!loading && !members.length && <p className="swarm-muted">{t("noAssignedSpecialists")}</p>}
    <div className="swarm-pagination"><button disabled={!offset || loading || Boolean(busy)} onClick={() => setOffset(n => Math.max(0, n - 50))}>{t("previous")}</button><button disabled={pageSize < 50 || loading || Boolean(busy)} onClick={() => setOffset(n => n + 50)}>{t("next")}</button></div>
  </>;
}
