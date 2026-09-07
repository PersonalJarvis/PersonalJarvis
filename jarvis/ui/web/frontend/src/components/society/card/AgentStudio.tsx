import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import * as Tabs from "@radix-ui/react-tabs";
import { Activity, Check, Cpu, MessageSquare, Palette, Pause, Play, Save, Shield, SlidersHorizontal, UserRound } from "lucide-react";
import { useLocaleChunk, useT } from "@/i18n";
import { PERMISSION_CEILINGS } from "@/lib/societyApi";
import { rowToAgent, useSetAgentPaused, type SocietyAgent } from "../data";
import { useAgentActivity } from "../cardData";
import type { FigureRecipe } from "../figures/figureRecipe";
import { LeadBrain, LeadInstructions } from "./LeadSections";
import { RetireButton } from "./RetireButton";
import { AgentStudioRoutines } from "./AgentStudioRoutines";
import { AgentStudioAppearance } from "./AgentStudioAppearance";
import { AgentStudioModel } from "./AgentStudioModel";
import { saveStudioAgent, studioDraft, studioPatch, validStudioLimits, type StudioDraft } from "./agentStudioData";
import "./agentStudio.css";

const SECTIONS = [
  { id: "profile", icon: UserRound }, { id: "model", icon: Cpu },
  { id: "appearance", icon: Palette }, { id: "limits", icon: Shield }, { id: "activity", icon: Activity },
] as const;

export interface AgentStudioProps {
  agent: SocietyAgent;
  sample?: boolean;
  onOpenChat: () => void;
  onRetired: () => void;
  onPreview: (recipe: FigureRecipe | null) => void;
  onGuardChange: (dirty: boolean, busy: boolean) => void;
}

/** A fresh editing surface; the roster and the figure share the saved identity. */
export function AgentStudio({ agent, sample = false, onOpenChat, onRetired, onPreview, onGuardChange }: AgentStudioProps) {
  useLocaleChunk("society");
  const t = useT();
  const id = useId();
  const client = useQueryClient();
  const setPaused = useSetAgentPaused();
  const [base, setBase] = useState(() => studioDraft(agent));
  const [draft, setDraft] = useState(base);
  const [section, setSection] = useState("profile");
  const [modelOpened, setModelOpened] = useState(false);
  const [activityOpened, setActivityOpened] = useState(false);
  const [modelGuard, setModelGuard] = useState({ dirty: false, busy: false });
  const [routineGuard, setRoutineGuard] = useState({ dirty: false, busy: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const previous = useRef(agent);
  const dirty = JSON.stringify(base) !== JSON.stringify(draft);
  const guard = useCallback((dirty: boolean, busy: boolean) => setModelGuard({ dirty, busy }), []);
  const guardRoutine = useCallback((dirty: boolean, busy: boolean) => setRoutineGuard({ dirty, busy }), []);
  useEffect(() => { onGuardChange(dirty || modelGuard.dirty || routineGuard.dirty, busy || modelGuard.busy || routineGuard.busy); }, [dirty, modelGuard, routineGuard, busy, onGuardChange]);
  useEffect(() => { onPreview(draft.figure); }, [draft.figure, onPreview]);
  // Background status polling may refresh the row, but never replaces an open draft.
  useEffect(() => {
    if (previous.current === agent) return;
    previous.current = agent;
    if (dirty || busy) return;
    const next = studioDraft(agent);
    setBase(next);
    setDraft(next);
  }, [agent, busy, dirty]);

  const edit = <K extends keyof StudioDraft>(key: K, value: StudioDraft[K]) => {
    setDraft((current) => ({ ...current, [key]: value })); setSaved(false); setError("");
  };
  const save = async () => {
    if (busy || !dirty || !validStudioLimits(draft) || sample) return;
    setBusy(true); setError("");
    try {
      const row = await saveStudioAgent(agent.agentId, studioPatch(base, draft));
      const next = studioDraft(rowToAgent(row));
      setBase(next); setDraft(next); setSaved(true);
      void client.invalidateQueries({ queryKey: ["society", "roster"] });
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };
  const paused = agent.lifecycle === "paused" || agent.state === "paused";
  const togglePaused = async () => {
    setBusy(true); setError("");
    try { await setPaused(agent, !paused); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };
  const lead = agent.tier === "lead";
  return <div className="agent-studio" data-testid="agent-studio">
    <header className="as-header">
      <div className="as-eyebrow"><SlidersHorizontal size={14} aria-hidden />{t("society.studio.eyebrow")}</div>
      <h2>{t("society.studio.heading")}</h2>
      <p>{t("society.studio.subtitle")}</p>
      <div className="as-identity"><span>{agent.name}</span><span>{t(`society.tier.${agent.tier}`)}</span><span className="as-status">{t(`society.state.${agent.state}`)}</span></div>
    </header>
    <Tabs.Root value={section} onValueChange={(value) => { setSection(value); if (value === "model") setModelOpened(true); if (value === "activity") setActivityOpened(true); }} className="as-tabs">
      <Tabs.List className="as-tab-list" aria-label={t("society.studio.sections")}>
        {SECTIONS.map(({ id, icon: Icon }) => <Tabs.Trigger className="as-tab" value={id} key={id}><Icon size={16} aria-hidden /><span>{t(`society.studio.${id}`)}</span></Tabs.Trigger>)}
      </Tabs.List>
      <div className="as-scroll">
        <Tabs.Content value="profile" className="as-panel">
          <div className="as-section-title"><h3>{t("society.studio.profile_heading")}</h3><span>01 / 05</span></div>
          <fieldset className="as-stack" disabled={busy || sample}>
            <label className="as-field"><span>{t("society.studio.role")}</span><input value={draft.title} onChange={(event) => edit("title", event.target.value)} placeholder={t("society.studio.role_placeholder")} /></label>
            {!lead ? <label className="as-field"><span>{t("society.card.description")}</span>
              <textarea value={draft.description} onChange={(event) => edit("description", event.target.value)} rows={8} placeholder={t("society.studio.instructions_placeholder")} />
              <small>{t("society.studio.instructions_hint")}</small>
            </label> : <LeadInstructions />}
          </fieldset>
          <div className="as-note"><Shield size={16} aria-hidden /><span>{t("society.studio.tools_hint")}</span></div>
          <button type="button" className="as-secondary as-wide" onClick={onOpenChat}><MessageSquare size={16} aria-hidden />{t("society.studio.chat_action")}</button>
        </Tabs.Content>
        <Tabs.Content value="model" forceMount={modelOpened ? true : undefined} className="as-panel">
          <div className="as-section-title"><h3>{t("society.studio.model_heading")}</h3><span>02 / 05</span></div>
          {lead ? <><LeadBrain /><button className="as-secondary" type="button" onClick={onOpenChat}>{t("society.studio.chat_action")}</button></> : modelOpened ? <AgentStudioModel agent={agent} sample={sample} onGuardChange={guard} /> : null}
        </Tabs.Content>
        <Tabs.Content value="appearance" className="as-panel">
          <div className="as-section-title"><h3>{t("society.studio.appearance_heading")}</h3><span>03 / 05</span></div>
          <fieldset disabled={busy}><AgentStudioAppearance recipe={draft.figure} lead={lead} onChange={(recipe) => edit("figure", recipe)} /></fieldset>
        </Tabs.Content>
        <Tabs.Content value="limits" className="as-panel">
          <div className="as-section-title"><h3>{t("society.studio.limits_heading")}</h3><span>04 / 05</span></div>
          <fieldset disabled={busy || sample} className="as-stack">
            <fieldset className="as-stack"><legend className="as-label">{t("society.card.permission")}</legend>
              <div className="as-ceilings">{PERMISSION_CEILINGS.map((ceiling) => <label key={ceiling} className="as-ceiling" data-selected={draft.ceiling === ceiling}>
                <input type="radio" name={`${id}-ceiling`} value={ceiling} checked={draft.ceiling === ceiling} onChange={() => edit("ceiling", ceiling)} />
                <span><strong>{t(`society.ceiling.${ceiling}`)}</strong><small>{t(`society.studio.ceiling_${ceiling}`)}</small></span>
              </label>)}</div>
            </fieldset>
            <div className="as-grid">
              <label className="as-field"><span>{t("society.studio.budget")}</span><input type="number" min="0" step="0.01" value={draft.dailyBudget} onChange={(event) => edit("dailyBudget", event.target.value)} /><small>{t("society.studio.budget_hint")}</small></label>
              <label className="as-field"><span>{t("society.studio.concurrency")}</span><input type="number" min="1" step="1" value={draft.concurrentRuns} onChange={(event) => edit("concurrentRuns", event.target.value)} /><small>{t("society.studio.concurrency_hint")}</small></label>
            </div>
            {!validStudioLimits(draft) ? <p className="as-error" role="alert">{t("society.studio.invalid_limits")}</p> : null}
          </fieldset>
          <div className="as-note">{t("society.studio.spent_today").replace("{amount}", agent.stats.spentTodayUsd.toFixed(2))}</div>
        </Tabs.Content>
        <Tabs.Content value="activity" forceMount={activityOpened ? true : undefined} className="as-panel">
          <div className="as-section-title"><h3>{t("society.studio.activity_heading")}</h3><span>05 / 05</span></div>
          <div className="as-stats"><div><strong>{agent.stats.runs}</strong><span>{t("society.card.runs")}</span></div><div><strong>${agent.stats.totalCostUsd.toFixed(2)}</strong><span>{t("society.studio.total_spent")}</span></div></div>
          {activityOpened ? <AgentStudioRoutines agentId={agent.agentId} sample={sample} onGuardChange={guardRoutine} /> : null}
          <StudioActivity agentId={agent.agentId} />
          <div className="as-paths"><label>{t("society.card.workspace")}<code>{agent.workspaceDir}</code></label><label>{t("society.studio.notes")}<code>{agent.wikiNamespace}</code></label></div>
          <div className="as-section-title"><h3>{t("society.studio.manage")}</h3></div>
          <div className="as-actions"><button type="button" className="as-secondary" disabled={busy || modelGuard.busy || sample} onClick={() => void togglePaused()}>
            {paused ? <Play size={15} aria-hidden /> : <Pause size={15} aria-hidden />}{t(paused ? "society.card.resume" : "society.card.pause")}
          </button><fieldset disabled={busy || modelGuard.busy || routineGuard.busy || dirty || modelGuard.dirty || routineGuard.dirty || sample}><RetireButton agent={agent} onRetired={onRetired} /></fieldset></div>
        </Tabs.Content>
      </div>
    </Tabs.Root>
    <footer className="as-footer">
      {error ? <p role="alert" className="as-error">{error}</p> : null}
      {sample ? <p className="as-note">{t("society.studio.sample")}</p> : null}
      <div className="as-save-row"><span className="as-save-status" role="status">
        {saved && !dirty ? <><Check size={15} aria-hidden />{t("society.studio.saved")}</> : t(dirty ? "society.studio.unsaved" : "society.studio.ready")}
      </span><button className="as-secondary" type="button" disabled={!dirty || busy} onClick={() => { setDraft(base); setSaved(false); setError(""); }}>{t("society.studio.discard")}</button>
        <button type="button" className="as-primary" disabled={!dirty || busy || sample || !validStudioLimits(draft)} onClick={() => void save()}><Save size={15} aria-hidden />{t(busy ? "society.card.saving" : "society.studio.save")}</button>
      </div>
    </footer>
  </div>;
}

function StudioActivity({ agentId }: { agentId: string }) {
  const t = useT();
  const activity = useAgentActivity(agentId);
  return <div className="as-stack"><h3 className="as-label">{t("society.studio.recent")}</h3>
    {activity.isLoading ? <p className="as-note">{t("society.studio.loading")}</p> : activity.isError ? <p className="as-error" role="alert">{t("society.studio.load_error")}</p> : !activity.data?.events.length ? <p className="as-note">{t("society.studio.no_activity")}</p> :
      <ul className="as-activity">{activity.data.events.slice(0, 8).map((entry) => <li key={entry.id}><span>{entry.type}</span><p>{entry.fromAgent} → {entry.toAgent ?? agentId}</p><time dateTime={new Date(entry.tsMs).toISOString()}>{new Date(entry.tsMs).toLocaleString()}</time></li>)}</ul>}
  </div>;
}
