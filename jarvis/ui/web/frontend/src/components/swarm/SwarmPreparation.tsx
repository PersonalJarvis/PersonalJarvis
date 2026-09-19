import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { requestConnect } from "@/lib/connectBudget";
import { answerPreparation, beginPreparation, launchPreparation, readPreparation } from "./preparationApi";
import type { PreparationView } from "./preparationTypes";
import { useSwarmText } from "./strings";
import { exactCount, microUsd, type TeamRecord } from "./types";

export function SwarmPreparation({ team, awake, onChanged }: {
  team: TeamRecord;
  awake: boolean;
  onChanged: (team: TeamRecord) => void;
}) {
  const t = useSwarmText();
  const [view, setView] = useState<PreparationView | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [readCompleted, setReadCompleted] = useState(0);
  const readFailures = useRef(0);
  const requests = useRef(0);
  const inFlight = useRef(false);
  const mutation = useRef<AbortController | null>(null);
  const retryKey = useRef({ signature: "", key: "" });
  const teamId = team.id;
  const generation = team.storage_generation ?? "";
  const load = useCallback(async (signal: AbortSignal) => {
    const serial = ++requests.current;
    try {
      const next = await readPreparation(teamId, signal);
      if (!signal.aborted && serial === requests.current) { readFailures.current = 0; setView(next); setError(""); }
    } catch (failure) {
      if (!signal.aborted && serial === requests.current) { readFailures.current += 1; setError(String(failure)); }
    } finally {
      if (!signal.aborted && serial === requests.current) setReadCompleted(value => value + 1);
    }
  }, [teamId]);
  useEffect(() => {
    if (!awake) return;
    const abort = new AbortController();
    void load(abort.signal);
    return () => abort.abort();
  }, [load, awake, generation, refresh]);
  useEffect(() => {
    if (!awake || busy || !view?.busy) return;
    const abort = new AbortController();
    const delay = Math.min(60000, 2000 * 2 ** Math.min(readFailures.current, 5));
    const cancel = requestConnect(() => { if (!inFlight.current) void load(abort.signal); }, delay + Math.random() * 1000);
    return () => { cancel(); abort.abort(); };
  }, [load, awake, busy, view, readCompleted]);
  useEffect(() => () => mutation.current?.abort(), []);
  useEffect(() => { if (view) setAnswers(view.answers); }, [view?.revision]);
  const keyFor = (signature: string) => {
    if (retryKey.current.signature !== signature) retryKey.current = { signature, key: crypto.randomUUID() };
    return retryKey.current.key;
  };
  async function act(kind: "begin" | "answers" | "launch") {
    if (inFlight.current || (kind !== "begin" && !view)) return;
    inFlight.current = true; setBusy(true); setError(""); ++requests.current;
    const abort = new AbortController(); mutation.current = abort;
    try {
      const signature = JSON.stringify({ kind, revision: view?.revision, generation, answers: kind === "answers" ? answers : undefined, digest: kind === "launch" ? view?.digest : undefined });
      const key = keyFor(signature);
      const next = kind === "begin" ? await beginPreparation(team, key, abort.signal)
        : kind === "answers" ? await answerPreparation(view!, answers, key, abort.signal)
          : await launchPreparation(view!, key, abort.signal);
      if (!abort.signal.aborted) {
        ++requests.current; setView(next); setEditing(false);
        retryKey.current = { signature: "", key: "" };
        onChanged(next.team);
      }
    } catch (failure) {
      if (!abort.signal.aborted) { setError(String(failure)); setRefresh(value => value + 1); }
    } finally {
      inFlight.current = false;
      if (!abort.signal.aborted) setBusy(false);
    }
  }
  function submitAnswers(event: FormEvent) { event.preventDefault(); void act("answers"); }
  const waiting = busy || view?.busy;
  const showQuestions = view && view.revision > 0 && (view.state === "clarifying" || editing || (view.state === "failed" && view.questions.length > 0));
  const canAnswer = view && view.questions.every(question => answers[question.id]?.trim());
  return <section className="swarm-panel swarm-preparation" aria-label={t("preparation")} aria-busy={Boolean(waiting)}>
    <div className="swarm-row"><h2>{t("preparation")}</h2><button disabled={busy} onClick={() => setRefresh(value => value + 1)}>{t("refresh")}</button></div>
    <p className="swarm-muted">{t("preparationHint")}</p>
    <ol className="swarm-preparation-steps" aria-label={t("preparationSteps")}>
      <li aria-current={showQuestions ? "step" : undefined}>{t("clarifyGoal")}</li>
      <li aria-current={view?.state === "ready" && !editing ? "step" : undefined}>{t("reviewPlan")}</li>
      <li aria-current={view?.state === "launched" ? "step" : undefined}>{t("launch")}</li>
    </ol>
    {(error || view?.error) && <p role="alert" className="swarm-error">{error || view?.error}</p>}
    {waiting && <p role="status">{busy ? t("loading") : t("preparationWorking")}</p>}
    {!view && !error && <p>{t("loading")}</p>}
    {((!view && error) || view?.revision === 0 || (view?.state === "failed" && !view.questions.length)) && <button disabled={Boolean(waiting)} onClick={() => void act("begin")}>{view?.revision === 0 && !view.error ? t("clarifyGoal") : t("retryPreparation")}</button>}
    {showQuestions && <form onSubmit={submitAnswers} aria-label={t("clarifyGoal")}>
      {!view.questions.length && <p>{t("noClarificationNeeded")}</p>}
      {view.questions.map(question => <div className="swarm-question" key={question.id}>
        <label htmlFor={`swarm-question-${teamId}-${question.id}`}>{question.prompt}</label>
        {question.hint && <p className="swarm-muted">{question.hint}</p>}
        {question.choices?.length ? <div className="swarm-actions">{question.choices.map(choice => <button type="button" key={choice} disabled={Boolean(waiting)} aria-pressed={answers[question.id] === choice} onClick={() => setAnswers(previous => ({ ...previous, [question.id]: choice }))}>{choice}</button>)}</div> : null}
        <textarea id={`swarm-question-${teamId}-${question.id}`} rows={2} maxLength={10000} value={answers[question.id] ?? ""} disabled={Boolean(waiting)} onChange={event => setAnswers(previous => ({ ...previous, [question.id]: event.target.value }))} required />
        <button type="button" disabled={Boolean(waiting)} onClick={() => setAnswers(previous => ({ ...previous, [question.id]: t("delegateClarificationAnswer") }))}>{t("delegateClarification")}</button>
      </div>)}
      <div className="swarm-actions"><button className="swarm-primary" disabled={Boolean(waiting) || !canAnswer} type="submit">{t("buildPlan")}</button>
        {editing && <button type="button" disabled={Boolean(waiting)} onClick={() => { setAnswers(view.answers); setEditing(false); }}>{t("backToPlan")}</button>}</div>
    </form>}
    {view?.plan && view.state === "ready" && !editing && <div className="swarm-plan-review">
      <h3>{t("reviewPlan")}</h3><p>{view.plan.summary}</p>
      <h3>{t("goal")}</h3><p className="swarm-plan-text">{view.plan.goal}</p>
      <h3>{t("acceptance")}</h3><p className="swarm-plan-text">{view.plan.acceptance}</p>
      {view.plan.assumptions.length > 0 && <><h3>{t("planAssumptions")}</h3><ul>{view.plan.assumptions.map((item, index) => <li key={index}>{item}</li>)}</ul></>}
      {view.plan.exclusions.length > 0 && <><h3>{t("planExclusions")}</h3><ul>{view.plan.exclusions.map((item, index) => <li key={index}>{item}</li>)}</ul></>}
      <h3>{t("planFirstSteps")}</h3><ol>{view.plan.tasks.map(task => <li key={task.id}><strong>{task.title}</strong><p>{task.description}</p><p className="swarm-muted">{task.acceptance}</p></li>)}</ol>
      {view.plan.remaining_decomposition && <p className="swarm-muted">{t("planCanExpand")}</p>}
      <details><summary>{t("advancedOptions")}</summary>
        <p>{t("budget")}: {exactCount(view.team.limits.token_budget)} · {t("runtime")}: {view.team.limits.runtime_seconds / 60}</p>
        {view.team.limits.monetary_limit_microusd !== null && <p>{t("costCeiling")}: {microUsd(view.team.limits.monetary_limit_microusd)}</p>}
        <p>{t("internet")}: {view.team.policy.internet ? view.team.policy.allowed_domains.join(", ") || t("publicSites") : t("offline")}</p>
      </details>
      <p className="swarm-muted">{t("approvePlanHint")}</p>
      <div className="swarm-actions"><button disabled={Boolean(waiting)} onClick={() => setEditing(true)}>{t("editAnswers")}</button><button className="swarm-primary" disabled={Boolean(waiting) || !view.digest} onClick={() => void act("launch")}>{t("approveAndLaunch")}</button></div>
    </div>}
    {view?.state === "launched" && <p role="status">{t("preparationLaunched")}</p>}
  </section>;
}
