import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { BrandedSelect } from "@/components/ui/select";
import { useCanvasAwake } from "@/hooks/useCanvasAwake";
import { useEventStore } from "@/store/events";
import { requestConnect } from "@/lib/connectBudget";
import { capabilities, controlTeam, listTeams, request, teamPath } from "@/components/swarm/api";
import { CreateTeamForm } from "@/components/swarm/CreateTeamForm";
import { DistributedSettings } from "@/components/swarm/DistributedSettings";
import { SwarmInspector } from "@/components/swarm/SwarmInspector";
import { SpecialistAssignments } from "@/components/swarm/SpecialistAssignments";
import { SwarmRequests } from "@/components/swarm/SwarmRequests";
import { SwarmStorage } from "@/components/swarm/SwarmStorage";
import { SwarmPreparation } from "@/components/swarm/SwarmPreparation";
import { SwarmResult } from "@/components/swarm/SwarmResult";
import { useSwarmText } from "@/components/swarm/strings";
import { useSwarmWorld } from "@/components/swarm/useSwarmWorld";
import { SwarmSimulation } from "@/components/swarm/SwarmSimulation";
import { allowedControls, budgetPercent, exactCount, isTeamUnavailable, microUsd, terminalState, type Capabilities, type Control, type RecordKind, type TeamListItem, type TeamRecord } from "@/components/swarm/types";
import "@/components/swarm/swarm.css";

// Loading the Swarm section does not allocate a renderer or import the Society world.
const SwarmWorld3D = lazy(() => import("@/components/swarm/SwarmWorld3D"));
const initialTeam = () => new URLSearchParams(window.location.search).get("swarm_team") ?? "";
export function teamUrl(id: string): string {
  const url = new URL(window.location.href);
  url.searchParams.set("view", "ultra-swarm");
  url.searchParams.set("swarm_team", id);
  return url.toString();
}
function TeamWorkspace({ teamId, catalogTeam, capability, onTeam, onRecheck, onRestored, onDeleted }: {
  teamId: string; catalogTeam?: TeamListItem; capability: Capabilities | null; onTeam: (team: TeamRecord) => void; onRecheck: () => void; onRestored: (team: TeamRecord) => void; onDeleted: (id: string) => void;
}) {
  const t = useSwarmText();
  const host = useRef<HTMLElement>(null);
  // The desktop WebView can report a visible document as hidden. Geometry is
  // the same trusted signal used by the existing app canvases.
  const awake = useCanvasAwake(host);
  const [group, setGroup] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [kind, setKind] = useState<RecordKind>("agents");
  const [selected, setSelected] = useState("");
  const [flat, setFlat] = useState(true);
  const [unavailable, setUnavailable] = useState(() => typeof WebGLRenderingContext === "undefined");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [metadata, setMetadata] = useState<TeamRecord | null>(null);
  const [metadataError, setMetadataError] = useState("");
  const knownUnavailable = isTeamUnavailable(catalogTeam);
  const { snapshot, connection, error: connectionError } = useSwarmWorld(teamId, group, refresh, awake && !knownUnavailable);
  const team = knownUnavailable ? undefined : snapshot?.team;
  const generation = team?.storage_generation ?? "";
  const details = metadata?.id === teamId && (metadata.storage_generation ?? "") === generation ? metadata : team;
  const preparationRequired = team?.state === "created" && Boolean(details?.checkpoint.preparation || (catalogTeam && !isTeamUnavailable(catalogTeam) && catalogTeam.checkpoint.preparation));
  const metadataReady = metadata?.id === teamId && (metadata.storage_generation ?? "") === generation;
  useEffect(() => {
    if (knownUnavailable) return;
    const abort = new AbortController();
    setMetadataError("");
    request<TeamRecord>(teamPath(teamId), { signal: abort.signal }).then(value => {
      if (value.id !== teamId) throw new Error("Rejected mismatched team details");
      if (!abort.signal.aborted) setMetadata(value);
    }).catch(failure => { if (!abort.signal.aborted) setMetadataError(String(failure)); });
    return () => abort.abort();
  }, [teamId, generation, refresh, knownUnavailable]);
  const storageUnavailable = !team && isTeamUnavailable(catalogTeam) ? catalogTeam : undefined;
  const markUnavailable = useCallback(() => setUnavailable(true), []);
  const chooseRecord = useCallback((nextKind: RecordKind, id: string) => { setKind(nextKind); setSelected(id); }, []);
  const inspectArea = useCallback((nextKind: "artifacts" | "publications") => { setKind(nextKind); setSelected(""); }, []);
  const preparationChanged = useCallback((next: TeamRecord) => {
    setMetadata(next); onTeam(next); setRefresh(value => value + 1); onRecheck();
  }, [onTeam, onRecheck]);
  const chooseNode = useCallback((id: string, nextGroup?: string) => {
    if (nextGroup !== undefined) { setGroup(nextGroup); setSelected(""); }
    else { setKind("agents"); setSelected(id); }
  }, []);
  useEffect(() => { if (team) onTeam(team); }, [team, onTeam]);
  async function control(action: Control) {
    if (!team || busy) return;
    setBusy(action); setError("");
    try { onTeam(await controlTeam(team, action)); setRefresh(n => n + 1); }
    catch (failure) { setError(String(failure)); setRefresh(n => n + 1); }
    finally { setBusy(""); }
  }
  const sandbox = capability?.sandbox as { available?: boolean; reason?: string } | undefined;
  return <main className="swarm-main" data-team-id={teamId} ref={host}>
    <section className="swarm-panel">
      <div className="swarm-row"><h2>{preparationRequired ? t("yourGoal") : team?.name ?? catalogTeam?.name ?? t("world")}</h2>{!preparationRequired && <span className="swarm-muted" role="status">{t(connection)}</span>}</div>
      {storageUnavailable && <><p className="swarm-notice"><strong>{t("unavailable")}</strong> · {t("teamUnavailableHint")}</p><a href={teamUrl(teamId)} target="_blank" rel="noopener noreferrer">{t("separateWindow")}</a></>}
      {team && <>
        <div className="swarm-row"><span className="swarm-state" data-state={team.state}>{t(team.state)}</span>
          <div className="swarm-actions">{allowedControls(team.state).filter(action => action !== "start" || (metadataReady && !preparationRequired)).map(action => <button key={action} disabled={Boolean(busy)} className={action === "start" || action === "resume" ? "swarm-primary" : action === "stop" || action === "cancel" ? "swarm-danger" : ""} onClick={() => void control(action)}>{t(action)}</button>)}
            <a href={teamUrl(teamId)} target="_blank" rel="noopener noreferrer">{t("separateWindow")}</a></div></div>
        <p className="swarm-goal">{details?.goal}</p>
        {details?.acceptance && <details><summary>{t("acceptance")}</summary><p className="swarm-goal">{details.acceptance}</p></details>}
        {team.reason && <p className="swarm-notice">{t("reason")}: {team.reason}</p>}
        <SwarmResult team={team} awake={awake} onInspect={chooseRecord} />
        <details key={preparationRequired ? "preparation-limits" : "execution-limits"} className="swarm-run-details" open={!preparationRequired}><summary>{t("runDetails")}</summary>
        <div className="swarm-metrics">
          <div className="swarm-metric"><span className="swarm-muted">{t("budget")}</span><strong>{exactCount(team.tokens_used)} / {exactCount(team.limits.token_budget)}</strong>
            <progress className="swarm-meter" aria-label={t("budget")} max={100} value={budgetPercent(team.tokens_used, team.tokens_reserved, team.limits.token_budget)} /><span className="swarm-muted">{t("reserved")}: {exactCount(team.tokens_reserved)}</span></div>
          <div className="swarm-metric"><span className="swarm-muted">{t("cost")}</span><strong>{microUsd(team.cost_microusd)}</strong><span className="swarm-muted">{t("reserved")}: {microUsd(team.cost_reserved_microusd)}{team.limits.monetary_limit_microusd !== null && ` / ${microUsd(team.limits.monetary_limit_microusd)}`}</span></div>
          <div className="swarm-metric"><span className="swarm-muted">{t("concurrency")}</span><strong>{team.limits.concurrency} / {exactCount(team.limits.worker_limit)}</strong><span className="swarm-muted">{t("network")}: {exactCount(team.network_bytes)}</span></div>
        </div>
        <p className="swarm-muted">{t("stopHint")}</p>
        <details><summary>{t("policy")}</summary><p className="swarm-goal">{details?.policy.internet ? details.policy.allowed_domains.join(", ") || t("publicSites") : t("offline")}</p>
          <p className="swarm-muted">{t("runtime")}: {team.limits.runtime_seconds / 60} · {t("dependencies")}: {t(details?.policy.allow_dependencies ? "enabled" : "disabled")}</p></details>
        <button onClick={() => { setKind("checkpoints"); setSelected(""); }}>{t("checkpoints")}</button>
        </details>
        {metadataError && <p className="swarm-error" role="alert">{metadataError}</p>}
        {terminalState(team.state) && <p className="swarm-notice">{t("finalWorld")}</p>}
      </>}
      {!team && connection === "loading" && <p>{t("loading")}</p>}
      {sandbox?.available === false && <p className="swarm-notice">{t("sandboxUnavailable")}: {sandbox.reason}</p>}
      {(error || connectionError || storageUnavailable?.error) && <p role="alert" className="swarm-error">{error || connectionError || storageUnavailable?.error}</p>}
      <SwarmStorage team={team ?? catalogTeam} teamId={teamId} onRestored={onRestored} onDeleted={onDeleted} onChanged={() => { setRefresh(n => n + 1); onRecheck(); }} />
    </section>
    {preparationRequired && details && <SwarmPreparation key={`${teamId}:${generation}`} team={details} awake={awake} onChanged={preparationChanged} />}
    {snapshot && team && !preparationRequired && <>
      <SpecialistAssignments team={snapshot.team} onChanged={() => { setRefresh(n => n + 1); onRecheck(); }} />
      <section className="swarm-panel" aria-label={t("world")}>
        <div className="swarm-row"><h2>{t("world")}</h2><div className="swarm-actions">
          {group && <button onClick={() => { setGroup(""); setSelected(""); }}>{t("back")}</button>}
          {!unavailable && <button onClick={() => setFlat(value => !value)}>{t(flat ? "threeD" : "flat")}</button>}
        </div></div>
        <p className="swarm-muted">{Object.entries(snapshot.counts).map(([state, count]) => `${t(state)}: ${exactCount(count)}`).join(" · ")}</p>
        {snapshot.aggregated && <p className="swarm-muted">{t("aggregation")}</p>}
        {flat || unavailable ? <><div className="swarm-actions" role="group" aria-label={t("memoryHouse")}>
          <button aria-pressed={kind === "artifacts"} onClick={() => inspectArea("artifacts")}>{t("artifacts")}: {exactCount(snapshot.counts.artifacts ?? "")}</button>
          <button aria-pressed={kind === "publications"} onClick={() => inspectArea("publications")}>{t("publications")}: {exactCount(snapshot.counts.publications ?? "")}</button>
        </div><SwarmSimulation snapshot={snapshot} selected={selected} onAgent={chooseNode} onRecord={chooseRecord} /></> : <Suspense fallback={<p>{t("loading")}</p>}><SwarmWorld3D snapshot={snapshot} selected={selected} onSelect={chooseNode} onInspect={inspectArea} onUnavailable={markUnavailable} live={connection === "live"} /></Suspense>}
        {snapshot.has_more && <p className="swarm-muted">{t("bounded")}</p>}
      </section>
      <SwarmInspector snapshot={snapshot} kind={kind} selected={selected} onSelect={chooseRecord} awake={awake} />
    </>}
  </main>;
}

export function UltraSwarmView() {
  const t = useSwarmText();
  const root = useRef<HTMLDivElement>(null);
  const awake = useCanvasAwake(root);
  const [teams, setTeams] = useState<TeamListItem[]>([]);
  const [capability, setCapability] = useState<Capabilities | null>(null);
  const [teamId, setTeamId] = useState(initialTeam);
  const [creating, setCreating] = useState(false);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const navigate = useEventStore(state => state.setActiveSection);
  useEffect(() => {
    const abort = new AbortController();
    setLoading(true); setError("");
    listTeams(offset, abort.signal).then(data => { if (!abort.signal.aborted) setTeams(data); })
      .catch(failure => { if (!abort.signal.aborted) setError(String(failure)); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [offset, refresh]);
  useEffect(() => {
    if (!awake) return;
    const abort = new AbortController();
    let cancelPoll: (() => void) | undefined;
    let failures = 0;
    const poll = async () => {
      try {
        const rows = await listTeams(offset, abort.signal);
        if (!abort.signal.aborted) { setTeams(rows); failures = 0; }
      } catch (failure) {
        if (!abort.signal.aborted) { setError(String(failure)); failures += 1; }
      } finally {
        if (!abort.signal.aborted) schedule();
      }
    };
    const schedule = () => { cancelPoll = requestConnect(() => void poll(), Math.min(60000, 5000 * 2 ** Math.min(failures, 4)) + Math.random() * 1000); };
    schedule();
    return () => { abort.abort(); cancelPoll?.(); };
  }, [offset, awake, refresh]);
  useEffect(() => {
    const abort = new AbortController();
    capabilities(abort.signal).then(data => { if (!abort.signal.aborted) setCapability(data); })
      .catch(failure => { if (!abort.signal.aborted) setError(String(failure)); });
    return () => abort.abort();
  }, [refresh]);
  useEffect(() => { const update = () => setTeamId(initialTeam()); window.addEventListener("popstate", update); return () => window.removeEventListener("popstate", update); }, []);
  const updateTeam = useCallback((team: TeamRecord) => setTeams(rows => rows.map(row => row.id === team.id ? team : row)), []);
  const recheckTeams = useCallback(() => setRefresh(n => n + 1), []);
  function selectTeam(id: string) { setTeamId(id); setCreating(false); window.history.replaceState(window.history.state, "", teamUrl(id)); }
  function restoredTeam(team: TeamRecord) { setOffset(0); setRefresh(n => n + 1); selectTeam(team.id); }
  function deletedTeam(id: string) {
    setTeams(rows => rows.filter(team => team.id !== id)); setRefresh(n => n + 1);
    if (teamId === id) {
      setTeamId("");
      const url = new URL(window.location.href); url.searchParams.delete("swarm_team");
      window.history.replaceState(window.history.state, "", url.toString());
    }
  }
  const filtered = teams.filter(team => filter === "all" || (isTeamUnavailable(team) ? filter === "active" : (filter === "history") === terminalState(team.state)));
  const catalogTeam = teams.find(team => team.id === teamId);
  const catalogGeneration = catalogTeam && !isTeamUnavailable(catalogTeam) ? catalogTeam.storage_generation ?? "" : "";
  const providerList = capability?.providers;
  const noProvider = Array.isArray(providerList) && !providerList.some(provider => provider.available === true);
  return <div className="swarm-root" ref={root}>
    <header className="swarm-row"><div><h1>{t("title")}</h1><p className="swarm-muted">{t("subtitle")}</p></div><button className="swarm-primary" onClick={() => setCreating(true)}>{t("newTeam")}</button></header>
    {noProvider && <p className="swarm-notice">{t("providerUnavailable")} <button onClick={() => navigate("apikeys")}>{t("settings")}</button></p>}
    {error && <p className="swarm-error" role="alert">{error} <button onClick={() => setRefresh(n => n + 1)}>{t("refresh")}</button></p>}
    <div className="swarm-layout"><aside className="swarm-sidebar" aria-label={t("teams")}>
      <div className="swarm-row"><h2>{t("teams")}</h2><button onClick={() => setRefresh(n => n + 1)} disabled={loading}>{t("refresh")}</button></div>
      <BrandedSelect ariaLabel={t("filter")} value={filter} onValueChange={setFilter} options={["all", "active", "history"].map(value => ({ value, label: t(value) }))} />
      <div className="swarm-team-list" aria-busy={loading}>{filtered.map(team => <button className="swarm-team" key={team.id} aria-current={teamId === team.id} onClick={() => selectTeam(team.id)}><strong>{team.name}</strong><span className="swarm-state" data-state={isTeamUnavailable(team) ? undefined : team.state}>{t(isTeamUnavailable(team) ? "unavailable" : team.state)}</span></button>)}</div>
      <div className="swarm-pagination"><button disabled={!offset || loading} onClick={() => setOffset(n => Math.max(0, n - 50))}>{t("previous")}</button><button disabled={teams.length < 50 || loading} onClick={() => setOffset(n => n + 50)}>{t("next")}</button></div>
    </aside>
      {creating || (!teamId && !loading) ? <CreateTeamForm capability={capability} onCreated={team => { setOffset(0); setRefresh(n => n + 1); selectTeam(team.id); }} onClose={teamId ? () => setCreating(false) : undefined} /> : teamId ? <TeamWorkspace key={`${teamId}:${isTeamUnavailable(catalogTeam) ? "unavailable" : "available"}:${catalogGeneration}`} teamId={teamId} catalogTeam={catalogTeam} capability={capability} onTeam={updateTeam} onRecheck={recheckTeams} onRestored={restoredTeam} onDeleted={deletedTeam} /> : <p>{t("loading")}</p>}
    </div>
    <details className="swarm-secondary"><summary>{t("swarmRequests")}</summary><SwarmRequests capability={capability} onCreated={team => { setOffset(0); setRefresh(n => n + 1); selectTeam(team.id); }} onSelect={selectTeam} /></details>
    <DistributedSettings onSaved={() => setRefresh(n => n + 1)} />
    {!teamId && <details className="swarm-secondary"><summary>{t("storage")}</summary><SwarmStorage onRestored={restoredTeam} onChanged={recheckTeams} onDeleted={deletedTeam} /></details>}
  </div>;
}
