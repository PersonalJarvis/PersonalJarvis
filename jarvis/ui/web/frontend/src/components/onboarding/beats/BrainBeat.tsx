import { ChevronDown, ExternalLink, Lock } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ApiKeyForm } from "@/components/ApiKeyForm";
import { FOCUS_RING } from "@/components/agentic/controls";
import { switchBrainProvider, useProviders, type ProviderDescriptor } from "@/hooks/useProviders";
import {
  applyStarterPlan,
  getStarterPlans,
  selectStarterPlan,
  type ApplyPlanOutcome,
  type StarterPlan,
} from "@/hooks/useStarterPlans";
import { fill, useT } from "@/i18n";
import { setLocalMode } from "@/lib/localMode";
import { cn } from "@/lib/utils";
import { putVoiceMode } from "@/lib/voiceEngineMode";
import {
  cardForPlan,
  planKeysComplete,
  planText,
  primarySlot,
  slotConfigured,
  slotEffective,
  startableProviders,
} from "../brainPlans";
import type { BeatProps } from "../WelcomeFlow";
import { Option, PrimaryAction, QuietAction, Rise, Status } from "../ui";

/** The "I'll pick the provider myself" choice; mirrors the backend's custom id. */
const CUSTOM = "custom";
const LOCAL = "local";

type Probe = "checking" | "ready" | "empty" | "missing";

/**
 * One key and the assistant can think and talk.
 *
 * The recommended way is a starter plan: pick OpenAI or Gemini, paste the one
 * key, and the plan points live voice and its thinking model at it — the key
 * form is the same one the API Keys view uses, and it tests the key the moment
 * it is saved. "Another provider" opens the whole brain catalog instead (any
 * single key must work, AP-21), and a machine that already runs Ollama can
 * start with no key at all.
 *
 * Nothing blocks: "Later" moves on and the ready beat says what stays off.
 */
export function BrainBeat({ next, skip, report, cheer }: BeatProps) {
  const t = useT();
  const { providers, loading, error, refetch } = useProviders();
  const [plans, setPlans] = useState<StarterPlan[]>([]);
  const [choice, setChoice] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [applied, setApplied] = useState<{ id: string; outcome: ApplyPlanOutcome } | null>(null);
  const [openRow, setOpenRow] = useState<string | null>(null);
  const [probe, setProbe] = useState<Probe>("checking");
  const [localActive, setLocalActive] = useState(false);
  const [localBusy, setLocalBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await getStarterPlans();
        if (cancelled) return;
        setPlans(res.plans);
        setChoice(
          (current) =>
            current ??
            (res.plans.some((p) => p.id === res.selected) ? res.selected : null) ??
            res.plans.find((p) => p.recommended)?.id ??
            CUSTOM,
        );
      } catch {
        // An older backend without plans: the full provider list still works.
        if (!cancelled) setChoice((current) => current ?? CUSTOM);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Is Ollama running here? Asked once, through the backend (a direct
  // browser call to localhost:11434 would be blocked anyway).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch("/api/providers/ollama/models");
        const data = res.ok ? await res.json() : null;
        const live = data?.source === "live" || data?.source === "cache";
        const count = Array.isArray(data?.models) ? data.models.length : 0;
        if (!cancelled) setProbe(live ? (count > 0 ? "ready" : "empty") : "missing");
      } catch {
        if (!cancelled) setProbe("missing");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const plan = useMemo(() => plans.find((p) => p.id === choice) ?? null, [plans, choice]);
  const startable = useMemo(() => startableProviders(providers), [providers]);
  const planCard = plan ? cardForPlan(plan, startable) : null;
  const planComplete = plan ? planKeysComplete(plan, startable) : false;
  const planApplied = Boolean(
    plan && applied?.id === plan.id && applied.outcome.modeSet && applied.outcome.failed.length === 0,
  );
  const configured = startable.filter(slotEffective);

  // The moment the plan's key is in, point live voice and its thinking model
  // at it. Once per plan; a re-render never re-applies.
  useEffect(() => {
    if (!plan || !planComplete || applying || applied?.id === plan.id) return;
    let cancelled = false;
    setApplying(true);
    void (async () => {
      try {
        await selectStarterPlan(plan.id).catch(() => undefined);
        const outcome = await applyStarterPlan(plan);
        if (cancelled) return;
        setApplied({ id: plan.id, outcome });
        if (outcome.failed.length === 0) cheer("jump");
      } finally {
        if (!cancelled) setApplying(false);
        void refetch();
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan?.id, planComplete]);

  const ready =
    choice === LOCAL
      ? localActive
      : plan
        ? planApplied
        : configured.length > 0;

  useEffect(() => {
    if (ready) {
      const summary =
        choice === LOCAL
          ? t("first_run.brain.summary_local")
          : plan
            ? planText(t, plan, "label")
            : configured.map((p) => p.label).join(" · ");
      report({ summary, gap: null });
    } else if (plan && applied?.id === plan.id && applied.outcome.failed.length > 0) {
      report({
        summary: planText(t, plan, "label"),
        gap: fill(t("first_run.brain.gap_partial"), {
          parts: applied.outcome.failed.map((f) => f.surface).join(", "),
        }),
      });
    } else {
      report({ summary: null, gap: t("first_run.brain.gap_none") });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, choice, plan?.id, applied, configured.map((p) => p.id).join(","), t]);

  // "Another provider" opens on the first card, so the list shows an input
  // and not a row of closed doors.
  useEffect(() => {
    if (choice === CUSTOM && openRow === null && startable.length > 0 && configured.length === 0) {
      setOpenRow(startable[0].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [choice, startable.length]);

  async function choose(id: string) {
    setChoice(id);
    if (id !== LOCAL) await selectStarterPlan(id).catch(() => undefined);
  }

  function activateIfNone(p: ProviderDescriptor) {
    if (startable.some((q) => q.active)) return;
    void switchBrainProvider(p.id)
      .then(() => {
        cheer("jump");
        return refetch();
      })
      .catch(() => {
        // The key is saved either way; API Keys stays the place to pick the brain.
      });
  }

  async function activateLocal() {
    setLocalBusy(true);
    setLocalError(null);
    try {
      await switchBrainProvider("ollama");
      // Realtime replaces listen-think-speak with one cloud model and never
      // asks the brain, so a local brain needs the pipeline mode as well.
      // Persisted, because the guide ends in a restart.
      await putVoiceMode("pipeline");
      // The provider console opens on the cards that need no key.
      setLocalMode(true);
      setLocalActive(true);
      cheer("jump");
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : String(e));
    } finally {
      setLocalBusy(false);
    }
  }

  const keyForm = (p: ProviderDescriptor, onSaved?: () => void) => {
    const slot = primarySlot(p);
    if (!slot) return null;
    return (
      <ApiKeyForm
        secretKey={slot}
        dashboardUrl={p.dashboard_url}
        configured={slotConfigured(p)}
        effectiveConfigured={slotEffective(p)}
        credentialHelp={p.credential_help}
        coveredNote={p.credential_note ?? null}
        sharedWith={p.secret_shared_with?.[slot] ?? []}
        testAfterSave={{ id: p.id, label: p.label, section: p.tier, active: p.active }}
        onChanged={() => void refetch()}
        onSavedActivate={onSaved}
      />
    );
  };

  return (
    <div className="space-y-5">
      {plans.length > 0 && (
        <Rise index={0}>
          <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label={t("first_run.brain.title")} data-testid="onboarding-plan-picker">
            {plans.map((p) => (
              <Option
                key={p.id}
                selected={choice === p.id}
                onSelect={() => void choose(p.id)}
                title={planText(t, p, "label")}
                body={planText(t, p, "summary")}
                badge={p.recommended ? t("first_run.brain.recommended") : null}
                testId={`onboarding-plan-${p.id}`}
              />
            ))}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">
            <QuietAction
              onClick={() => void choose(CUSTOM)}
              testId="onboarding-plan-custom"
              className={cn(choice === CUSTOM && "text-foreground underline underline-offset-4")}
            >
              {t("first_run.brain.other_provider")}
            </QuietAction>
            <QuietAction
              onClick={() => void choose(LOCAL)}
              testId="onboarding-plan-local"
              className={cn(choice === LOCAL && "text-foreground underline underline-offset-4")}
            >
              {probe === "ready" || probe === "empty"
                ? t("first_run.brain.local_found")
                : t("first_run.brain.local")}
            </QuietAction>
          </div>
        </Rise>
      )}

      {loading && startable.length === 0 && choice !== LOCAL ? (
        <Status tone="muted">{t("first_run.brain.loading")}</Status>
      ) : error && startable.length === 0 && choice !== LOCAL ? (
        <Status tone="warning">{t("first_run.brain.load_failed")}</Status>
      ) : null}

      {plan && planCard && (
        <Rise index={1}>
          <div className="rounded-xl border border-border bg-background p-4" data-testid="onboarding-live-key">
            <div className="mb-3 flex items-baseline justify-between gap-3">
              <span className="text-sm font-medium text-foreground">
                {fill(t("first_run.brain.key_for"), { provider: planCard.label })}
              </span>
              <span className="text-xs text-muted-foreground">
                {slotEffective(planCard) ? t("first_run.brain.key_saved") : t("first_run.brain.key_missing")}
              </span>
            </div>
            {keyForm(planCard)}
          </div>
        </Rise>
      )}

      {choice === CUSTOM && startable.length > 0 && (
        <Rise index={1}>
          <ol className="overflow-hidden rounded-xl border border-border bg-background" data-testid="onboarding-provider-list">
            {startable.map((p) => {
              const open = openRow === p.id;
              const effective = slotEffective(p);
              return (
                <li key={p.id} className="border-b border-border last:border-b-0">
                  <button
                    type="button"
                    aria-expanded={open}
                    data-testid={`onboarding-provider-${p.id}`}
                    onClick={() => setOpenRow(open ? null : p.id)}
                    className={cn(
                      "flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-secondary",
                      FOCUS_RING,
                    )}
                  >
                    <span className="min-w-0 flex-1 text-sm font-medium text-foreground">{p.label}</span>
                    {p.recommended && (
                      <span className="text-micro font-medium text-accent">{t("first_run.brain.recommended")}</span>
                    )}
                    <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <span
                        aria-hidden
                        className={cn("h-1.5 w-1.5 rounded-full", effective ? "bg-success" : "bg-border-strong")}
                      />
                      {p.active
                        ? t("first_run.brain.active")
                        : effective
                          ? t("first_run.brain.key_saved")
                          : t("first_run.brain.key_missing")}
                    </span>
                    <ChevronDown
                      aria-hidden
                      className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", open && "rotate-180")}
                    />
                  </button>
                  {open && <div className="px-4 pb-4">{keyForm(p, () => activateIfNone(p))}</div>}
                </li>
              );
            })}
          </ol>
        </Rise>
      )}

      {choice === LOCAL && (
        <Rise index={1}>
          <div className="space-y-3 rounded-xl border border-border bg-background p-4" data-testid="onboarding-local">
            <p className="text-sm font-medium text-foreground">{t("first_run.brain.local_title")}</p>
            {probe === "checking" && <Status tone="muted">{t("first_run.brain.local_checking")}</Status>}
            {probe === "ready" && !localActive && (
              <p className="text-sm leading-relaxed text-muted-foreground">{t("first_run.brain.local_ready")}</p>
            )}
            {probe === "empty" && (
              <p className="text-sm leading-relaxed text-muted-foreground">{t("first_run.brain.local_empty")}</p>
            )}
            {probe === "missing" && (
              <p className="text-sm leading-relaxed text-muted-foreground">
                {t("first_run.brain.local_missing")}{" "}
                <a
                  href="https://ollama.com/download"
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-accent underline-offset-4 hover:underline"
                >
                  {t("first_run.brain.local_get")}
                  <ExternalLink aria-hidden className="h-3 w-3" />
                </a>
              </p>
            )}
            {probe === "ready" && !localActive && (
              <button
                type="button"
                onClick={() => void activateLocal()}
                disabled={localBusy}
                className={cn(
                  "h-9 rounded-lg border border-border-strong bg-card px-4 text-sm font-medium text-foreground transition-colors hover:bg-secondary disabled:opacity-40",
                  FOCUS_RING,
                )}
              >
                {t("first_run.brain.local_use")}
              </button>
            )}
            {localActive && <Status tone="ok">{t("first_run.brain.local_active")}</Status>}
            {localError && <Status tone="error">{localError}</Status>}
          </div>
        </Rise>
      )}

      {plan && applying && (
        <Status tone="muted" testId="onboarding-plan-applying">
          {fill(t("first_run.brain.applying"), { plan: planText(t, plan, "label") })}
        </Status>
      )}
      {plan && planApplied && (
        <Status tone="ok" testId="onboarding-plan-applied">
          {fill(t("first_run.brain.applied"), { plan: planText(t, plan, "label") })}
        </Status>
      )}
      {plan && applied?.id === plan.id && applied.outcome.failed.length > 0 && (
        <Status tone="warning" testId="onboarding-plan-partial">
          {fill(t("first_run.brain.partial"), {
            parts: applied.outcome.failed.map((f) => `${f.surface}: ${f.error}`).join(" · "),
          })}
        </Status>
      )}

      <Rise index={2}>
        <p className="flex items-start gap-2 text-xs leading-relaxed text-muted-foreground">
          <Lock aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {t("first_run.brain.security")}
        </p>
      </Rise>

      <Rise index={3} className="space-y-3">
        <PrimaryAction onClick={next} disabled={!ready}>
          {t("first_run.continue")}
        </PrimaryAction>
        {!ready && (
          <div className="text-center">
            <QuietAction onClick={skip} testId="onboarding-brain-later">
              {t("first_run.brain.later")}
            </QuietAction>
          </div>
        )}
      </Rise>
    </div>
  );
}
