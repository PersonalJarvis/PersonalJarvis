import { useEffect, useRef, useState } from "react";
import { request, teamPath } from "./api";
import { validSkills, type AgentSkills, type ContributionRecheck, type MeasuredRate } from "./reputationTypes";
import { useSwarmText } from "./strings";
import { exactCount } from "./types";

export function measuredRateText(value: MeasuredRate, unknown: string): string {
  if (value.denominator === null || value.numerator === null) return unknown;
  const rate = value.rate === null ? unknown : `${(value.rate * 100).toFixed(1)}%`;
  return `${rate} (${exactCount(value.numerator)} / ${exactCount(value.denominator)})`;
}

export function SwarmSkills({ teamId, agentId, awake, onTask }: {
  teamId: string; agentId: string; awake: boolean; onTask: (id: string) => void;
}) {
  const t = useSwarmText();
  const [data, setData] = useState<AgentSkills | null>(null);
  const [error, setError] = useState("");
  const [offset, setOffset] = useState(0);
  useEffect(() => { setOffset(0); setData(null); }, [teamId, agentId]);
  useEffect(() => {
    if (!awake) return;
    const abort = new AbortController(); setData(null); setError("");
    request<AgentSkills>(`${teamPath(teamId)}/agents/${encodeURIComponent(agentId)}/skills?limit=50&offset=${offset}`, { signal: abort.signal })
      .then(value => { if (!validSkills(value, teamId, agentId)) throw new Error("Mismatched or invalid Swarm skills"); if (!abort.signal.aborted) setData(value); })
      .catch(failure => { if (!abort.signal.aborted) setError(String(failure)); });
    return () => abort.abort();
  }, [teamId, agentId, awake, offset]);
  const profile = data?.team_id === teamId && data.agent_id === agentId ? data : null;
  return <section aria-label={t("skillProfile")}>
    <h4>{t("skillProfile")}</h4>
    {error && <p className="swarm-error" role="alert">{error}</p>}
    {!profile && !error && <p className="swarm-muted">{t("loading")}</p>}
    {profile?.profiles.map(skill => <section key={skill.domain}>
      <h4>{skill.domain} · {t("level")} {skill.level}</h4>
      {skill.next_level_credits !== null && skill.next_level_credits > 0 && <label>{t("skillProgress")}
        <progress aria-label={t("skillProgress")} value={skill.credits} max={skill.next_level_credits} />
        <span> {skill.credits.toLocaleString(undefined, { maximumFractionDigits: 2 })} / {skill.next_level_credits}</span>
      </label>}
      <dl>
        <div><dt>{t("reliability")}</dt><dd>{(skill.reliability * 100).toFixed(1)}%</dd></div>
        <div><dt>{t("skillUncertainty")}</dt><dd>± {(skill.uncertainty * 100).toFixed(1)}% · {exactCount(skill.samples)} {t("skillSamples")}</dd></div>
        <div><dt>{t("skillAcceptance")}</dt><dd>{measuredRateText(skill.acceptance, t("unmeasured"))}</dd></div>
        <div><dt>{t("skillRegression")}</dt><dd>{measuredRateText(skill.regression, t("unmeasured"))}</dd></div>
        <div><dt>{t("skillRollback")}</dt><dd>{measuredRateText(skill.rollback, t("unmeasured"))}</dd></div>
        <div><dt>{t("skillDifficulty")}</dt><dd>{Object.entries(skill.difficulty_counts).map(([level, count]) => `${level}: ${exactCount(count)}`).join(" · ")}</dd></div>
      </dl>
    </section>)}
    {profile && <div className="swarm-pagination"><button disabled={!offset} onClick={() => setOffset(n => Math.max(0, n - 50))}>{t("previous")}</button><button disabled={!profile.has_more} onClick={() => setOffset(n => n + 50)}>{t("next")}</button></div>}
    {!!profile?.history.length && <><h4>{t("skillHistory")}</h4><p className="swarm-muted">{t("skillEvidenceHint")}</p>
      {profile.history.map(change => <div key={change.id}>
        <button onClick={() => onTask(change.task_id)}>{change.domain} · {t("level")} {change.previous_level} → {change.level}</button>
        <p>{change.reason} · {new Date(change.created_at * 1000).toLocaleString()}</p>
      </div>)}
    </>}
  </section>;
}

export function SwarmRecheck({ teamId, taskId, onEvidence }: {
  teamId: string; taskId: string; onEvidence: (id: string) => void;
}) {
  const t = useSwarmText();
  const [result, setResult] = useState<ContributionRecheck | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);
  const requestKey = useRef<string | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  async function recheck() {
    abort.current?.abort();
    const controller = new AbortController(); abort.current = controller;
    setPending(true); setError("");
    try {
      requestKey.current ??= crypto.randomUUID();
      const value = await request<ContributionRecheck>(`${teamPath(teamId)}/tasks/${encodeURIComponent(taskId)}/recheck`, { method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ request_key: requestKey.current }) });
      if (value.team_id !== teamId || value.task_id !== taskId) throw new Error("Mismatched Swarm recheck");
      if (!controller.signal.aborted) { setResult(value); if (value.state !== "running") requestKey.current = null; }
    } catch (failure) { if (!controller.signal.aborted) setError(String(failure)); }
    finally { if (!controller.signal.aborted) setPending(false); }
  }
  return <section aria-label={t("recheck")}>
    <p className="swarm-muted">{t("recheckHint")}</p>
    <button disabled={pending} onClick={() => void recheck()}>{pending ? t("rechecking") : t("recheck")}</button>
    {error && <p role="alert" className="swarm-error">{error}</p>}
    {result && <><p role="status">{t(`recheck_${result.state}`)}: {result.reason}</p>
      <div className="swarm-actions">{result.evidence_ids.map(id => <button key={id} onClick={() => onEvidence(id)}>{t("evidence")} · {id.slice(0, 8)}</button>)}</div>
    </>}
  </section>;
}
