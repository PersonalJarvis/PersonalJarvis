/**
 * The model card's spec column — a champion card of the island.
 *
 * It used to be a stack of grey headings whose most important line read "No
 * focus tools" above an empty list: `GRANTED TOOLS` only ever filled for an
 * allow-list agent, and almost every agent runs `grant_mode = all`. So the
 * card said nothing about the one thing that makes a Gmail agent a Gmail
 * agent.
 *
 * It now leads with HANDS — what this agent reaches for first, as tiles
 * carrying the services' real marks — and says in one quiet line that
 * everything else stays in reach, because it does (agent-definition §3.2).
 *
 * TWO RULES this file exists to keep (maintainer, 2026-09-03):
 *
 * 1. Nothing on the card is decoration. Every value shown is one a part of
 *    the system actually reads: the ceiling decides approvals
 *    (`society/approvals.decide`), the daily budget stops a dispatch
 *    (`society/scheduler`, which is why today's spend is drawn against it
 *    rather than the lifetime total), `max_concurrent_runs` caps runs in
 *    flight, focus orders the tool list. The one control that did nothing —
 *    "Assign task", disabled since it shipped — is gone rather than greyed.
 * 2. Nothing is empty because nobody asked. Routines, learned skills and the
 *    activity log come from routes that existed all along and that no
 *    frontend file was calling (`../cardData.ts`).
 *
 * The skin is in `agentCard.css`: the island's shape, the app's colours.
 * Jarvis keeps its two special sections — its brain is the app's chat brain
 * and its orders are the user's own instructions file.
 *
 * Every string goes through the locale files.
 */
import { useMemo, useState } from "react";
import { MessageSquare, Pause, Play } from "lucide-react";

import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { RetireButton } from "./RetireButton";
import { CapabilityChip } from "../CapabilityChip";
import { useAgentActivity, useAgentRoutines, useAgentSkills, type AgentActivity } from "../cardData";
import { PERMISSION_CEILINGS } from "@/lib/societyApi";

import {
  useSetAgentPaused,
  useSocietyCapabilities,
  useUpdateAgentDescription,
  useUpdateAgentLimits,
  type AgentRunState,
  type Capability,
  type PermissionCeiling,
  type SocietyAgent,
} from "../data";
import { CapabilityTile } from "./CapabilityTile";
import { LeadBrain, LeadInstructions } from "./LeadSections";

import "./agentCard.css";

/** How far up the three-step ladder a ceiling sits. `block` never reaches a card. */
const CEILING_STEP: Record<PermissionCeiling, number> = { safe: 1, monitor: 2, ask: 3 };

/** The state dot's fill — the same three status jobs the rest of the app uses. */
const STATE_FILL: Record<AgentRunState, string> = {
  idle: "bg-muted-foreground/40",
  working: "bg-success",
  waiting: "bg-warning",
  paused: "bg-muted-foreground/25",
};

const REACH_STEPS = 6;
/** More rows than this and the log stops being a glance. */
const LOG_ROWS = 6;

/**
 * The agent's standing instructions, editable in place. Rules mostly arrive
 * through the chat (the agent proposes, the person confirms), but a person may
 * also write them directly; the PATCH sends the description alone and the route
 * keeps the focus and approval rules the agent earned.
 */
function DescriptionEditor({ agent }: { agent: SocietyAgent }) {
  const t = useT();
  const update = useUpdateAgentDescription();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(agent.description);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const begin = () => {
    setDraft(agent.description);
    setError("");
    setEditing(true);
  };
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      await update(agent, draft.trim());
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };
  return (
    <>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="ac-head">{t("society.card.description")}</h3>
        {editing ? null : (
          <button
            type="button"
            onClick={begin}
            className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground"
          >
            {t("society.card.edit")}
          </button>
        )}
      </div>
      {editing ? (
        <div className="flex flex-col gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={Math.min(14, Math.max(4, draft.split(/\r?\n/).length + 1))}
            disabled={saving}
            className="w-full resize-y rounded-md border border-border bg-background px-2.5 py-2 text-sm leading-relaxed text-foreground outline-none focus:border-primary"
          />
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={saving}
              onClick={() => void save()}
              className="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {saving ? t("society.card.saving") : t("society.card.save")}
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => setEditing(false)}
              className="rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50"
            >
              {t("society.card.cancel")}
            </button>
            {error ? <span className="text-xs text-destructive">{error}</span> : null}
          </div>
        </div>
      ) : agent.description ? (
        <p className="whitespace-pre-line text-sm leading-relaxed">{agent.description}</p>
      ) : (
        <p className="text-xs text-muted-foreground">{t("society.card.no_description")}</p>
      )}
    </>
  );
}

function formatDate(ms: number | null, fallback: string): string {
  if (ms === null) return fallback;
  return new Date(ms).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}

/** "in 3 h", "in 2 d", "now" — from an epoch in ms. */
function untilLabel(ms: number, t: (key: string) => string): string {
  const delta = ms - Date.now();
  if (delta <= 60_000) return t("society.card.due_now");
  const minutes = Math.round(delta / 60_000);
  if (minutes < 60) return t("society.card.due_in").replace("{0}", `${minutes} min`);
  const hours = Math.round(minutes / 60);
  if (hours < 48) return t("society.card.due_in").replace("{0}", `${hours} h`);
  return t("society.card.due_in").replace("{0}", `${Math.round(hours / 24)} d`);
}

export function relativeUntil(iso: string | null, t: (key: string) => string): string | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : untilLabel(ms, t);
}

/** "2 min ago", "3 h ago" — the past half of the same ladder. */
function agoLabel(ms: number, t: (key: string) => string): string {
  const delta = Date.now() - ms;
  if (delta < 60_000) return t("society.card.just_now");
  const minutes = Math.round(delta / 60_000);
  const unit =
    minutes < 60
      ? `${minutes} min`
      : minutes < 2880
        ? `${Math.round(minutes / 60)} h`
        : `${Math.round(minutes / 1440)} d`;
  return t("society.card.ago").replace("{0}", unit);
}

export interface AgentSpecSheetProps {
  agent: SocietyAgent;
  /**
   * Hands the card back to its chat. The profile is the card's second face,
   * so "Chat" here is a real way back rather than the placeholder it was
   * while the chat had no place of its own.
   */
  onOpenChat?: () => void;
  /**
   * The agent was retired: the card closes so the island's ceremony
   * (`world/retirement.ts`) is actually visible behind it.
   */
  onRetired?: () => void;
}

export function AgentSpecSheet({ agent, onOpenChat, onRetired }: AgentSpecSheetProps) {
  const t = useT();
  const capabilities = useSocietyCapabilities();
  const activity = useAgentActivity(agent.agentId);
  const skills = useAgentSkills(agent.agentId);
  const routines = useAgentRoutines(agent.agentId);
  const setPaused = useSetAgentPaused();
  const [busy, setBusy] = useState(false);
  const byId = useMemo(() => {
    const map = new Map<string, Capability>();
    for (const c of capabilities.data ?? []) map.set(c.id, c);
    return map;
  }, [capabilities.data]);

  // The catalog is optional enrichment: the query does not retry, and a card
  // with no catalog still names every tool through the brand resolver. What
  // it must NOT do is print a total it does not have.
  const catalogSize = capabilities.data?.length ?? 0;
  const allowlist = agent.grantMode === "allowlist";

  /** What the agent reaches for first — its allow-list is its hands when it has no focus. */
  const hands = agent.focus.length > 0 ? agent.focus : allowlist ? agent.toolGrants : [];
  const paused = agent.lifecycle === "paused" || agent.state === "paused";

  const reachFilled = allowlist
    ? Math.max(1, Math.round((agent.toolGrants.length / Math.max(catalogSize, 1)) * REACH_STEPS))
    : REACH_STEPS;
  const reachValue = allowlist
    ? t("society.card.reach_allowlist")
        .replace("{0}", String(agent.toolGrants.length))
        .replace("{1}", String(catalogSize))
    : t("society.card.reach_all");

  // The budget is a real gate, so it is drawn as one: today's spend against
  // the cap the scheduler refuses at. A cap of 0 means "no cap" there, so it
  // means "no cap" here too rather than a bar that is always full.
  const capped = agent.dailyBudgetUsd > 0;
  const spentToday = agent.stats.spentTodayUsd;
  const spentShare = capped ? Math.min(1, spentToday / agent.dailyBudgetUsd) : 0;
  const exhausted = capped && spentToday >= agent.dailyBudgetUsd;

  const log = (activity.data?.events ?? []).slice(0, LOG_ROWS);
  const activeRuns = activity.data?.activeRuns ?? 0;
  const learned = skills.data ?? [];
  const schedule = routines.data ?? [];

  const togglePause = async () => {
    setBusy(true);
    try {
      await setPaused(agent, !paused);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ac-card" data-testid="agent-card-sheet">
      <header className="ac-band" data-tier={agent.tier}>
        <span className="min-w-0 flex-1">
          <span className="ac-band-name block truncate">{agent.name}</span>
          {/* Jarvis' title IS "Lead", so the tier would otherwise read twice. */}
          <span className="ac-band-title block truncate">
            {[t(`society.tier.${agent.tier}`), agent.title]
              .filter((part, i, all) => part && all.indexOf(part) === i)
              .join(" · ")}
          </span>
        </span>
        <span className={cn("ac-dot", STATE_FILL[agent.state])} data-state={agent.state} aria-hidden />
        <span className="shrink-0 text-xs text-muted-foreground">
          {activeRuns > 0
            ? t("society.card.active_runs").replace("{0}", String(activeRuns))
            : t(`society.state.${agent.state}`)}
        </span>
      </header>

      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col gap-5 p-5">
          <section>
            <h3 className="ac-head mb-2.5">{t("society.card.hands")}</h3>
            {hands.length === 0 ? (
              <p className="ac-prose text-xs leading-relaxed text-muted-foreground">
                {t("society.card.hands_empty")}
              </p>
            ) : (
              <ul className="flex flex-wrap gap-3">
                {hands.map((id) => (
                  <CapabilityTile
                    key={id}
                    id={id}
                    capability={byId.get(id)}
                    palette={agent.palette}
                    disconnectedHint={t("society.card.not_connected")}
                  />
                ))}
              </ul>
            )}
            <p className="ac-prose mt-3 text-xs leading-relaxed text-muted-foreground">
              {allowlist
                ? t("society.card.hands_rest_allowlist")
                : catalogSize > 0
                  ? t("society.card.hands_rest").replace("{0}", String(catalogSize))
                  : t("society.card.hands_rest_plain")}
            </p>
            {agent.denies.length > 0 ? (
              <div className="mt-3">
                <p className="ac-prose mb-1.5 text-xs text-muted-foreground">{t("society.card.denied")}</p>
                <div className="flex flex-wrap gap-1.5">
                  {agent.denies.map((id) => (
                    <CapabilityChip key={id} id={id} capability={byId.get(id)} className="line-through opacity-60" />
                  ))}
                </div>
              </div>
            ) : null}
          </section>

          {learned.length > 0 ? (
            <section>
              <h3 className="ac-head mb-2.5">{t("society.card.learned")}</h3>
              <ul className="flex flex-wrap gap-3">
                {learned.map((skill) => (
                  <CapabilityTile
                    key={skill.slug}
                    id={`skill:${skill.slug}`}
                    capability={{
                      id: `skill:${skill.slug}`,
                      kind: "skill",
                      label: skill.name,
                      one_liner: skill.whenToUse || skill.description,
                      risk_tier: "safe",
                      connected: true,
                      tool_name: "",
                    }}
                    palette={agent.palette}
                  />
                ))}
              </ul>
            </section>
          ) : null}

          <LimitsSection
            agent={agent}
            reachFilled={reachFilled}
            reachValue={reachValue}
            spentToday={spentToday}
            spentShare={spentShare}
            capped={capped}
            exhausted={exhausted}
          />

          <section className="grid grid-cols-2 gap-2">
            <Plate label={t("society.card.place")} value={t(`society.checkpoint.${agent.checkpoint}`)} />
            <Plate label={t("society.card.created")} value={formatDate(agent.createdMs, "—")} />
          </section>

          <section>
            <h3 className="ac-head mb-2">{t("society.card.brain")}</h3>
            {agent.tier === "lead" ? (
              <LeadBrain />
            ) : (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                {agent.provider ? (
                  <span className="inline-flex items-center gap-1.5">
                    <ProviderLogo
                      providerId={agent.provider}
                      label={agent.providerLabel || agent.provider}
                      size="sm"
                    />
                    <span className="font-medium">{agent.providerLabel || agent.provider}</span>
                  </span>
                ) : (
                  <span className="font-medium">{t("society.card.default_brain")}</span>
                )}
                {agent.model ? <span className="ac-prose font-mono text-xs">{agent.model}</span> : null}
                {agent.effort ? <span className="text-xs text-muted-foreground">{agent.effort}</span> : null}
              </div>
            )}
          </section>

          <section className="ac-prose">
            {agent.tier === "lead" ? <LeadInstructions /> : <DescriptionEditor agent={agent} />}
          </section>

          {agent.approvalRules.requireApproval.length > 0 || agent.approvalRules.alwaysAllow.length > 0 ? (
            <section>
              <h3 className="ac-head mb-2">{t("society.card.approval_rules")}</h3>
              {agent.approvalRules.requireApproval.length > 0 ? (
                <RuleRow label={t("society.card.require_approval")} ids={agent.approvalRules.requireApproval} byId={byId} />
              ) : null}
              {agent.approvalRules.alwaysAllow.length > 0 ? (
                <RuleRow label={t("society.card.always_allow")} ids={agent.approvalRules.alwaysAllow} byId={byId} />
              ) : null}
            </section>
          ) : null}

          <section>
            <h3 className="ac-head mb-2">{t("society.card.routines")}</h3>
            {schedule.length === 0 ? (
              <p className="ac-prose text-xs text-muted-foreground">{t("society.card.no_routines")}</p>
            ) : (
              <ul className="ac-prose flex flex-col gap-1.5">
                {schedule.map((routine) => (
                  <li key={routine.id} className="flex items-baseline justify-between gap-3 text-sm">
                    <span className="truncate">{routine.title}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {routine.schedule}
                      {routine.dueMs ? ` · ${untilLabel(routine.dueMs, t)}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <h3 className="ac-head mb-2">{t("society.card.recent")}</h3>
            {log.length === 0 ? (
              <p className="ac-prose text-xs text-muted-foreground">{t("society.card.no_recent")}</p>
            ) : (
              <div className="ac-log">
                {log.map((row) => (
                  <LogRow key={row.id} row={row} agentId={agent.agentId} t={t} />
                ))}
              </div>
            )}
          </section>

          <section>
            <h3 className="ac-head mb-2">{t("society.card.record")}</h3>
            <div className="grid grid-cols-3 gap-2">
              <Plate label={t("society.card.runs")} value={String(agent.stats.runs)} />
              <Plate label={t("society.card.cost")} value={`$${agent.stats.totalCostUsd.toFixed(2)}`} />
              <Plate
                label={t("society.card.last_active")}
                value={formatDate(agent.stats.lastActiveMs, t("society.card.never"))}
              />
            </div>
          </section>

          {agent.workspaceDir || agent.wikiNamespace ? (
            <section>
              <h3 className="ac-head mb-2">{t("society.card.where")}</h3>
              <div className="grid grid-cols-2 gap-2">
                {agent.workspaceDir ? (
                  <Plate label={t("society.card.workspace")} value={agent.workspaceDir} mono />
                ) : null}
                {agent.wikiNamespace ? (
                  <Plate label={t("society.card.wiki")} value={agent.wikiNamespace} mono />
                ) : null}
              </div>
            </section>
          ) : null}
        </div>
      </ScrollArea>

      <div className="ac-actions">
        <button
          type="button"
          className="ac-btn"
          disabled={!onOpenChat}
          title={onOpenChat ? undefined : t("society.card.chat_soon")}
          onClick={onOpenChat}
        >
          <MessageSquare className="h-3.5 w-3.5" aria-hidden />
          {t("society.card.action_chat")}
        </button>
        <button type="button" className="ac-btn ml-auto" disabled={busy} onClick={() => void togglePause()}>
          {paused ? <Play className="h-3.5 w-3.5" aria-hidden /> : <Pause className="h-3.5 w-3.5" aria-hidden />}
          {paused ? t("society.card.action_resume") : t("society.card.action_pause")}
        </button>
        {/* Last, and on its own: pausing is a Tuesday, retiring is forever. */}
        <RetireButton agent={agent} onRetired={onRetired} />
      </div>
    </div>
  );
}

/**
 * The three knobs a person may turn after the agent exists, plus the reach
 * they cannot (that one follows the grant mode).
 *
 * All three are enforced somewhere — the budget stops a dispatch, the ceiling
 * decides what runs without asking, the concurrency caps runs in flight — and
 * until now they could be set only once, at creation, behind the Advanced
 * disclosure, and never again (maintainer, 2026-09-03). Read mode is the
 * meters as before; Edit turns the same rows into controls and PATCHes the
 * three together.
 */
function LimitsSection({
  agent,
  reachFilled,
  reachValue,
  spentToday,
  spentShare,
  capped,
  exhausted,
}: {
  agent: SocietyAgent;
  reachFilled: number;
  reachValue: string;
  spentToday: number;
  spentShare: number;
  capped: boolean;
  exhausted: boolean;
}) {
  const t = useT();
  const update = useUpdateAgentLimits();
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [ceiling, setCeiling] = useState<PermissionCeiling>(agent.permissionCeiling);
  const [budget, setBudget] = useState(String(agent.dailyBudgetUsd));
  // The stored 0 IS 'no cap' (the scheduler skips the gate), so the switch
  // reads it back rather than asking the person to know that.
  const [budgetOn, setBudgetOn] = useState(agent.dailyBudgetUsd > 0);
  const [jobs, setJobs] = useState(String(agent.maxConcurrentRuns));

  const begin = () => {
    setCeiling(agent.permissionCeiling);
    setBudget(String(agent.dailyBudgetUsd || 2));
    setBudgetOn(agent.dailyBudgetUsd > 0);
    setJobs(String(agent.maxConcurrentRuns));
    setError("");
    setEditing(true);
  };
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      await update(agent, {
        permissionCeiling: ceiling,
        dailyBudgetUsd: budgetOn ? Math.max(0, Number.parseFloat(budget) || 0) : 0,
        maxConcurrentRuns: Number.parseInt(jobs, 10) || 1,
      });
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="flex flex-col gap-2">
      <div className="mb-0.5 flex items-center justify-between gap-2">
        <h3 className="ac-head">{t("society.card.limits")}</h3>
        {editing ? null : (
          <button
            type="button"
            onClick={begin}
            data-testid="agent-limits-edit"
            className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground"
          >
            {t("society.card.edit")}
          </button>
        )}
      </div>

      {editing ? (
        <div className="ac-prose flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">{t("society.card.permission")}</span>
            <div className="flex flex-wrap gap-1">
              {PERMISSION_CEILINGS.map((step) => (
                <button
                  key={step}
                  type="button"
                  disabled={saving}
                  onClick={() => setCeiling(step)}
                  aria-pressed={ceiling === step}
                  className={cn(
                    "rounded-md border px-2.5 py-1 text-xs transition-colors disabled:opacity-50",
                    ceiling === step
                      ? "border-border-strong bg-secondary text-foreground"
                      : "border-border text-muted-foreground hover:bg-secondary",
                  )}
                >
                  {t(`society.ceiling.${step}`)}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap items-end gap-4">
            <div className="flex flex-col gap-1">
              <span className="flex items-center gap-2 text-xs text-muted-foreground">
                <Switch
                  checked={budgetOn}
                  onCheckedChange={setBudgetOn}
                  disabled={saving}
                  aria-label={t("society.card.budget_cap")}
                  data-testid="agent-budget-switch"
                />
                {t("society.card.budget_cap")}
              </span>
              {budgetOn ? (
                <input
                  type="number"
                  min={0}
                  step={0.5}
                  inputMode="decimal"
                  disabled={saving}
                  value={budget}
                  onChange={(e) => setBudget(e.target.value)}
                  data-testid="agent-budget-input"
                  className="w-28 rounded-md border border-border bg-background px-2 py-1 font-mono text-sm text-foreground outline-none focus:border-primary disabled:opacity-50"
                />
              ) : (
                <span className="py-1 text-sm text-muted-foreground">{t("society.card.no_cap")}</span>
              )}
            </div>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">{t("society.card.jobs_at_once")}</span>
              <input
                type="number"
                min={1}
                step={1}
                inputMode="numeric"
                disabled={saving}
                value={jobs}
                onChange={(e) => setJobs(e.target.value)}
                className="w-20 rounded-md border border-border bg-background px-2 py-1 font-mono text-sm text-foreground outline-none focus:border-primary disabled:opacity-50"
              />
            </label>
          </div>
          <p className="text-xs text-muted-foreground">
            {budgetOn ? t("society.card.budget_hint") : t("society.card.budget_off_hint")}
          </p>

          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={saving}
              onClick={() => void save()}
              className="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {saving ? t("society.card.saving") : t("society.card.save")}
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => setEditing(false)}
              className="rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50"
            >
              {t("society.card.cancel")}
            </button>
            {error ? <span className="text-xs text-destructive">{error}</span> : null}
          </div>
        </div>
      ) : (
        <>
          <StepMeter
            label={t("society.card.permission")}
            steps={3}
            filled={CEILING_STEP[agent.permissionCeiling] ?? 0}
            value={t(`society.ceiling.${agent.permissionCeiling}`)}
          />
          <StepMeter
            label={t("society.card.meter_reach")}
            steps={REACH_STEPS}
            filled={reachFilled}
            value={reachValue}
          />
          <div className="ac-meter">
            <span className="ac-meter-label">{t("society.card.meter_today")}</span>
            <span
              className="ac-meter-bar"
              data-spent={exhausted ? "1" : "0"}
              role="img"
              aria-label={`${t("society.card.meter_today")}: $${spentToday.toFixed(2)}`}
            >
              <span className="ac-meter-fill" style={{ width: `${Math.round(spentShare * 100)}%` }} />
            </span>
            <span className="ac-meter-value">
              {capped
                ? t("society.card.spent_of")
                    .replace("{0}", spentToday.toFixed(2))
                    .replace("{1}", agent.dailyBudgetUsd.toFixed(2))
                : t("society.card.no_cap")}
            </span>
          </div>
          <div className="ac-meter">
            <span className="ac-meter-label">{t("society.card.jobs_at_once")}</span>
            <span className="ac-meter-value">{agent.maxConcurrentRuns}</span>
          </div>
        </>
      )}
    </section>
  );
}

/**
 * A value on a short discrete ladder, drawn as filled notches. Only used for
 * things that really are steps — the three permission ceilings, the share of
 * the catalog an allow-list keeps — never for a number dressed up as one.
 */
function StepMeter({
  label,
  steps,
  filled,
  value,
}: {
  label: string;
  steps: number;
  filled: number;
  value: string;
}) {
  return (
    <div className="ac-meter">
      <span className="ac-meter-label">{label}</span>
      <span className="ac-meter-track" role="img" aria-label={`${label}: ${value}`}>
        {Array.from({ length: steps }, (_, i) => (
          <span key={i} className="ac-meter-seg" data-on={i < filled ? "1" : "0"} />
        ))}
      </span>
      <span className="ac-meter-value">{value}</span>
    </div>
  );
}

function Plate({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="ac-plate" title={value}>
      <div className="ac-plate-label truncate">{label}</div>
      <div className={cn("ac-plate-value truncate", mono && "ac-prose font-mono text-xs")}>{value}</div>
    </div>
  );
}

/**
 * One line of the agent's own traffic. `events_for_agent` also carries the
 * board's broadcasts, so `cardData` filters those out first — a row here is
 * always something this agent sent or was sent.
 */
function LogRow({
  row,
  agentId,
  t,
}: {
  row: AgentActivity;
  agentId: string;
  t: (key: string) => string;
}) {
  const outgoing = row.fromAgent === agentId;
  const what = outgoing
    ? row.toAgent
      ? t("society.card.log_to").replace("{0}", row.toAgent)
      : t("society.card.log_board")
    : t("society.card.log_from").replace("{0}", row.fromAgent ?? "?");
  return (
    <div className="ac-log-row">
      <span className="ac-log-type">{row.type.toLowerCase()}</span>
      <span className="ac-log-what truncate">
        {what}
        {row.costUsd > 0 ? ` · $${row.costUsd.toFixed(2)}` : ""}
      </span>
      <span className="ac-log-when">{row.tsMs ? agoLabel(row.tsMs, t) : ""}</span>
    </div>
  );
}

function RuleRow({ label, ids, byId }: { label: string; ids: string[]; byId: Map<string, Capability> }) {
  return (
    <div className="mb-2">
      <p className="ac-prose mb-1 text-xs text-muted-foreground">{label}</p>
      <div className="flex flex-wrap gap-1.5">
        {ids.map((id) => (
          <CapabilityChip key={id} id={id} capability={byId.get(id.split(":").slice(0, 2).join(":"))} />
        ))}
      </div>
    </div>
  );
}
