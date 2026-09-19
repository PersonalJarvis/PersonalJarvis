import { useEffect, useState } from "react";
import { request } from "./api";
import { CreateTeamForm } from "./CreateTeamForm";
import { beginPreparation } from "./preparationApi";
import { useSwarmText } from "./strings";
import { exactCount, microUsd, type Capabilities, type TeamCreate, type TeamRecord } from "./types";

export interface SwarmProposal {
  id: string; source_agent_id: string; source_name: string; created_at: number;
  state: "pending" | "approving" | "approved" | "rejected"; team_id: string | null;
  brief: { name: string; goal: string; acceptance: string; authorized_input: string; request_key?: string };
  approval_spec?: TeamCreate;
}
const proposalPath = (id: string) => `/api/swarm/requests/${encodeURIComponent(id)}`;
const jsonPost = (body: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** Proposals require an owner-reviewed budget. Merely opening this inbox never starts a team. */
export function SwarmRequests({ capability, onCreated, onSelect }: {
  capability: Capabilities | null; onCreated: (team: TeamRecord) => void; onSelect: (id: string) => void;
}) {
  const t = useSwarmText();
  const [rows, setRows] = useState<SwarmProposal[]>([]);
  const [selected, setSelected] = useState("");
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    setLoading(true);
    request<SwarmProposal[]>(`/api/swarm/requests?limit=50&offset=${offset}`, { signal: abort.signal })
      .then(data => {
        if (!Array.isArray(data) || data.length > 50 || data.some(row => !row || typeof row.id !== "string" || typeof row.source_name !== "string" || !row.brief || ![row.brief.name, row.brief.goal, row.brief.acceptance, row.brief.authorized_input].every(value => typeof value === "string") || !["pending", "approving", "approved", "rejected"].includes(row.state))) throw new Error("Invalid Swarm proposal response");
        if (!abort.signal.aborted) setRows(data);
      })
      .catch(failure => { if (!abort.signal.aborted) setError(String(failure)); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [offset, refresh]);
  const proposal = rows.find(row => row.id === selected);
  async function approve(item: SwarmProposal, spec: TeamCreate): Promise<TeamRecord> {
    setBusy(item.id); setError(""); setNotice("");
    try {
      const team = await request<TeamRecord>(`${proposalPath(item.id)}/approve`, jsonPost(spec));
      setSelected(""); setNotice(t("proposalCreated"));
      return spec.preparation_required ? (await beginPreparation(team, `proposal:${spec.request_key}`)).team : team;
    } catch (failure) { setError(String(failure)); throw failure; }
    finally { setBusy(""); setRefresh(n => n + 1); }
  }
  async function resume(item: SwarmProposal) {
    if (busy || !item.approval_spec) return;
    // The durable approval already fixes every field and key. Never rebuild it from UI defaults.
    try { onCreated(await approve(item, item.approval_spec)); }
    catch { /* approve reports the error and refreshes the durable proposal state. */ }
  }
  async function reject(item: SwarmProposal) {
    if (busy) return;
    setBusy(item.id); setError(""); setNotice("");
    try {
      await request(`${proposalPath(item.id)}/reject`, { method: "POST" });
      setSelected(""); setNotice(t("proposalRejected")); setRefresh(n => n + 1);
    } catch (failure) { setError(String(failure)); setRefresh(n => n + 1); }
    finally { setBusy(""); }
  }
  return <section className="swarm-panel swarm-distributed" aria-label={t("swarmRequests")}>
    <div className="swarm-row"><h2>{t("swarmRequests")}</h2><button disabled={loading || Boolean(busy)} onClick={() => { setError(""); setRefresh(n => n + 1); }}>{t("refresh")}</button></div>
    <p className="swarm-muted">{t("proposalHint")}</p>
    {error && <p className="swarm-error" role="alert">{error}</p>}
    {notice && <p className="swarm-notice" role="status">{notice}</p>}
    {loading && <p className="swarm-muted">{t("loading")}</p>}
    {!loading && !rows.length && <p className="swarm-muted">{t("noProposals")}</p>}
    <div className="swarm-record-list">{rows.map(item => <article key={item.id} aria-label={item.brief.name}>
      <div className="swarm-row"><strong>{item.brief.name}</strong><span className="swarm-state">{t(`proposal_${item.state}`)}</span></div>
      <p className="swarm-muted">{t("requestedBy")}: {item.source_name}</p>
      <details><summary>{t("details")}</summary><p className="swarm-goal">{item.brief.goal}</p><p className="swarm-goal">{item.brief.acceptance}</p>
        <strong>{t("authorizedInput")}</strong><pre className="swarm-checkpoint">{item.brief.authorized_input || t("noAuthorizedInput")}</pre></details>
      {item.state === "approving" && item.approval_spec && <details><summary>{t("savedApproval")}</summary>
        <p className="swarm-goal"><strong>{item.approval_spec.name}</strong><br />{item.approval_spec.goal}<br />{item.approval_spec.acceptance}</p>
        <p className="swarm-muted">{t("budget")}: {exactCount(item.approval_spec.limits.token_budget)} · {t("money")}: {item.approval_spec.limits.monetary_limit_microusd === null ? "—" : microUsd(item.approval_spec.limits.monetary_limit_microusd)} · {t("workerLimit")}: {exactCount(item.approval_spec.limits.worker_limit)} · {t("concurrency")}: {item.approval_spec.limits.concurrency}</p>
        <p className="swarm-muted">{t("runtime")}: {item.approval_spec.limits.runtime_seconds / 60} · {t("mode")}: {t(item.approval_spec.mode)} · {t("internet")}: {t(item.approval_spec.policy.internet ? "enabled" : "disabled")}</p>
        <p className="swarm-goal">{t("domains")}: {item.approval_spec.policy.allowed_domains.join(", ") || t(item.approval_spec.policy.internet ? "publicSites" : "offline")}</p>
        <p className="swarm-goal">{t("tools")}: {item.approval_spec.policy.tools.map(tool => t(`tool_${tool}`)).join(", ")}</p>
      </details>}
      <div className="swarm-actions">
        {item.state === "pending" && <><button disabled={Boolean(busy)} onClick={() => { setSelected(item.id); setError(""); }}>{t("reviewProposal")}</button><button disabled={Boolean(busy)} onClick={() => void reject(item)}>{t("rejectProposal")}</button></>}
        {item.state === "approving" && <><p className="swarm-muted">{t("resumeApprovalHint")}</p><button disabled={Boolean(busy) || !item.approval_spec} onClick={() => void resume(item)}>{t("resumeApproval")}</button>{!item.approval_spec && <p className="swarm-error">{t("approvalSpecMissing")}</p>}</>}
        {item.state === "approved" && item.team_id && <button onClick={() => onSelect(item.team_id!)}>{t("openTeam")}</button>}
      </div>
    </article>)}</div>
    <div className="swarm-pagination"><button disabled={!offset || loading || Boolean(busy)} onClick={() => { setSelected(""); setOffset(n => Math.max(0, n - 50)); }}>{t("previous")}</button><button disabled={rows.length < 50 || loading || Boolean(busy)} onClick={() => { setSelected(""); setOffset(n => n + 50); }}>{t("next")}</button></div>
    {proposal?.state === "pending" && <>
      <p className="swarm-notice">{t("assignmentScopeHint")}</p><strong>{t("authorizedInput")}</strong><pre className="swarm-checkpoint">{proposal.brief.authorized_input || t("noAuthorizedInput")}</pre>
      <CreateTeamForm key={proposal.id} capability={capability} initial={proposal.brief} title={t("approveProposal")} submitLabel={t("approveAndCreate")} onSubmitTeam={spec => approve(proposal, spec)} onCreated={onCreated} onClose={() => setSelected("")} />
    </>}
  </section>;
}
