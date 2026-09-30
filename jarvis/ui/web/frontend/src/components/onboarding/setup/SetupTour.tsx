import { AlertTriangle, ArrowLeft, Cloud, CreditCard, Lock, Monitor, Terminal } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { MascotGigi, type MascotAction } from "@/components/MascotGigi";
import { Switch } from "@/components/ui/switch";
import type { useOnboarding } from "@/hooks/useOnboarding";
import { switchBrainProvider, useProviders } from "@/hooks/useProviders";
import { applyStarterPlan, getStarterPlans, selectStarterPlan, type StarterPlan } from "@/hooks/useStarterPlans";
import { useWakeWord } from "@/hooks/useWakeWord";
import { fill, setUiLanguage, useLocaleChunk, useT, useUiLanguage, type UiLanguage } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { planKeysComplete, slotEffective, startableProviders } from "../brainPlans";
import { ProgressDots } from "../ProgressDots";
import { Spotlight } from "../tour/Spotlight";
import { CheckLine, PrimaryAction, QuietAction, Status } from "../ui";
import { resumeStep, SETUP_STEPS, stepsFor, type SetupStepId } from "./setupSteps";
import { useAnchorRect } from "./useAnchorRect";

type Onb = ReturnType<typeof useOnboarding>;

const MASCOT: Record<SetupStepId, MascotAction> = {
  welcome: "wave",
  keys: "look-left",
  voice: "look-right",
  permissions: "look-left",
  ready: "jump",
};

const LANGS: UiLanguage[] = ["en", "de", "es"];

/**
 * First-run setup, done inside the real app.
 *
 * There is no setup screen of its own: the window dims, and the guide walks
 * the user to the places where each thing is really set — the API Keys page
 * for one key, the wake-word group in Settings, on macOS the permissions —
 * and waits there with a small card. The dim takes clicks, the hole does
 * not: only the part being set up can be used, so nothing else starts before
 * setup is done. Every step but the consent has a way on without doing it.
 *
 * The last step completes onboarding; the backend then restarts the app once
 * and the tour of the app follows. `preview` (a replay) never completes and
 * never restarts — it only walks the steps and hands over to the tour.
 */
export function SetupTour({
  onb,
  preview,
  onFinished,
}: {
  onb: Onb;
  preview: boolean;
  onFinished: () => void;
}) {
  const t = useT();
  const ready = useLocaleChunk("onboarding");
  const [platform, setPlatform] = useState<string | null>(null);
  const steps = useMemo(() => stepsFor(platform), [platform]);
  // A replay shows every step from the start; a real first run resumes where
  // it left off (never past the consent).
  const [stepId, setStepId] = useState<SetupStepId>(() =>
    preview
      ? "welcome"
      : resumeStep(stepsFor(null), onb.state?.current_step ?? null, Boolean(onb.state?.terms.accepted)),
  );
  const [skipped, setSkipped] = useState<string[]>(() => onb.state?.skipped_steps ?? []);
  const [cue, setCue] = useState(0);
  const step = SETUP_STEPS[stepId];

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch("/api/permissions/status");
        if (!res.ok) return;
        const data = (await res.json()) as { platform?: string };
        if (!cancelled && typeof data.platform === "string") setPlatform(data.platform);
      } catch {
        // Best-effort: without the probe there is simply no permissions step.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Open the app's own place for this step before pointing at it.
  useEffect(() => {
    if (!ready || !step.section) return;
    const nav = useEventStore.getState();
    if (nav.activeSection !== step.section) nav.setActiveSection(step.section);
  }, [ready, step.section, stepId]);

  const rect = useAnchorRect(ready ? step.anchor : undefined, Boolean(step.scrollTo), stepId);

  const cheer = useCallback(() => setCue((c) => c + 1), []);

  const goTo = useCallback(
    (target: SetupStepId, nextSkipped: string[]) => {
      setStepId(target);
      setCue((c) => c + 1);
      if (!preview) void onb.saveStep(target, nextSkipped);
    },
    [onb, preview],
  );

  const index = Math.max(0, steps.indexOf(stepId));
  const nextId = steps[index + 1] ?? null;
  const prevId = index > 1 ? steps[index - 1] : null; // never back behind the consent

  const next = useCallback(() => {
    if (nextId) goTo(nextId, skipped);
  }, [nextId, goTo, skipped]);

  const later = useCallback(() => {
    const nextSkipped = skipped.includes(stepId) ? skipped : [...skipped, stepId];
    setSkipped(nextSkipped);
    if (nextId) goTo(nextId, nextSkipped);
  }, [skipped, stepId, nextId, goTo]);

  if (!ready) return null;

  const footer = (
    <div className="mt-4 grid grid-cols-[1fr_auto_1fr] items-center gap-2">
      <div>
        {prevId && (
          <QuietAction
            onClick={() => goTo(prevId, skipped)}
            className="inline-flex items-center gap-1 text-xs"
            testId="setup-back"
          >
            <ArrowLeft aria-hidden className="h-3 w-3" />
            {t("first_run.back")}
          </QuietAction>
        )}
      </div>
      <ProgressDots count={steps.length} index={index} />
      <p className="text-right text-xs text-muted-foreground">
        {fill(t("first_run.step_of"), { current: index + 1, total: steps.length })}
      </p>
    </div>
  );

  return (
    <Spotlight rect={rect} placement={step.placement} blocking cardWidth={step.width}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="setup-title"
        className="rounded-2xl border border-border bg-popover p-5 text-popover-foreground shadow-float"
        data-testid="setup-card"
        data-step={stepId}
      >
        <div className="flex items-start gap-3">
          <MascotGigi size={40} reactToVoice={false} enableComments={false} cue={{ action: MASCOT[stepId], key: cue }} />
          <div className="min-w-0 flex-1">
            <h2 id="setup-title" className="text-base font-semibold tracking-tight text-foreground">
              {t(`first_run.${stepId}.title`)}
            </h2>
            <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{t(`first_run.${stepId}.lede`)}</p>
          </div>
        </div>
        <div className="mt-4">
          {stepId === "welcome" && <WelcomeStep onb={onb} preview={preview} onAccepted={() => { cheer(); next(); }} />}
          {stepId === "keys" && <KeysStep next={next} later={later} cheer={cheer} />}
          {stepId === "voice" && <VoiceStep next={next} />}
          {stepId === "permissions" && <PermissionsStep next={next} />}
          {stepId === "ready" && <ReadyStep onb={onb} preview={preview} onFinished={onFinished} />}
        </div>
        {footer}
      </div>
    </Spotlight>
  );
}

/* ------------------------------------------------------------------ steps */

function WelcomeStep({ onb, preview, onAccepted }: { onb: Onb; preview: boolean; onAccepted: () => void }) {
  const t = useT();
  const lang = useUiLanguage();
  const [accepted, setAccepted] = useState(Boolean(onb.state?.terms.accepted));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [declined, setDeclined] = useState(false);
  const [terms, setTerms] = useState<string | null>(null);
  const [showTerms, setShowTerms] = useState(false);

  async function toggleTerms() {
    setShowTerms((v) => !v);
    if (terms !== null) return;
    try {
      const res = await fetch("/api/onboarding/terms");
      setTerms(res.ok ? ((await res.json()) as { text: string }).text : "");
    } catch {
      setTerms("");
    }
  }

  async function proceed() {
    if (!accepted || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (!preview) await onb.acceptTerms();
      onAccepted();
    } catch {
      setError(t("first_run.welcome.accept_failed"));
    } finally {
      setBusy(false);
    }
  }

  async function decline() {
    // The goodbye shows first: the backend ends the process right after it answers.
    setDeclined(true);
    if (preview) return;
    try {
      await fetch("/api/onboarding/decline-terms", { method: "POST" });
    } catch {
      // A warming backend cannot hold the goodbye back; the window closes either way.
    }
  }

  if (declined) {
    return (
      <div className="space-y-1" data-testid="onboarding-declined">
        <p className="text-sm font-medium text-foreground">{t("first_run.welcome.declined_title")}</p>
        <p className="text-sm leading-relaxed text-muted-foreground">{t("first_run.welcome.declined_body")}</p>
      </div>
    );
  }

  const facts: { key: string; Icon: typeof Terminal }[] = [
    { key: "commands", Icon: Terminal },
    { key: "screen", Icon: Monitor },
    { key: "cloud", Icon: Cloud },
    { key: "costs", Icon: CreditCard },
    { key: "mistakes", Icon: AlertTriangle },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-0.5 self-start rounded-full border border-border p-0.5" role="radiogroup" aria-label={t("first_run.welcome.language")}>
        {LANGS.map((code) => (
          <button
            key={code}
            type="button"
            role="radio"
            aria-checked={lang === code}
            onClick={() => setUiLanguage(code)}
            data-testid={`onboarding-lang-${code}`}
            className={cn(
              "flex-1 rounded-full px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              lang === code ? "bg-secondary font-medium text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t(`first_run.welcome.lang_${code}`)}
          </button>
        ))}
      </div>

      <ul className="space-y-1.5 rounded-xl border border-border bg-background px-3.5 py-3" data-testid="onboarding-facts">
        {facts.map(({ key, Icon }) => (
          <li key={key} className="flex items-start gap-2.5 text-sm leading-snug text-foreground">
            <Icon aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span>{t(`first_run.welcome.fact_${key}`)}</span>
          </li>
        ))}
      </ul>

      <div className="space-y-1.5">
        <CheckLine checked={accepted} onChange={setAccepted} testId="onboarding-accept">
          {t("first_run.welcome.accept")}
        </CheckLine>
        <QuietAction onClick={() => void toggleTerms()} className="ml-7 text-xs underline underline-offset-4">
          {showTerms ? t("first_run.welcome.hide_terms") : t("first_run.welcome.read_terms")}
        </QuietAction>
        {showTerms && (
          <pre className="ml-7 max-h-32 overflow-y-auto whitespace-pre-wrap rounded-lg border border-border bg-background p-2.5 font-sans text-xs leading-relaxed text-muted-foreground scrollbar-jarvis">
            {terms || t("first_run.welcome.terms_loading")}
          </pre>
        )}
      </div>

      {error && <Status tone="error">{error}</Status>}

      <div className="space-y-2">
        <PrimaryAction onClick={() => void proceed()} disabled={!accepted} busy={busy}>
          {t("first_run.welcome.start")}
        </PrimaryAction>
        <div className="text-center">
          <QuietAction onClick={() => void decline()} testId="onboarding-decline" className="text-xs">
            {t("first_run.welcome.decline")}
          </QuietAction>
        </div>
      </div>
    </div>
  );
}

/**
 * The API Keys page is open behind the card. The step waits for a key to
 * land in any card there. A key saved during this step is also switched on:
 * a starter plan that key completes points live voice and its thinking model
 * at it; any other Brain key becomes the Brain when none is active yet. A key
 * that was already there is left exactly as it is.
 */
function KeysStep({ next, later, cheer }: { next: () => void; later: () => void; cheer: () => void }) {
  const t = useT();
  const { providers } = useProviders();
  const [plans, setPlans] = useState<StarterPlan[]>([]);
  const [savedSlot, setSavedSlot] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [connected, setConnected] = useState<string | null>(null);
  const [partial, setPartial] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getStarterPlans()
      .then((res) => {
        if (!cancelled) setPlans(res.plans);
      })
      .catch(() => {
        // No plans (older backend): a saved key still becomes the Brain.
      });
    const onSaved = (event: Event) => {
      const detail = (event as CustomEvent<{ key?: string; action?: string }>).detail;
      if (detail?.action === "set" && typeof detail.key === "string") setSavedSlot(detail.key);
    };
    window.addEventListener("jarvis:secret-configured", onSaved);
    return () => {
      cancelled = true;
      window.removeEventListener("jarvis:secret-configured", onSaved);
    };
  }, []);

  const withKey = providers.filter((p) => (p.secret_keys?.length ?? 0) > 0 && slotEffective(p));
  const localBrain = providers.some((p) => p.tier === "brain" && p.active && (p.secret_keys?.length ?? 0) === 0);
  const hasKey = withKey.length > 0 || localBrain;

  useEffect(() => {
    if (!savedSlot || connecting || connected) return;
    // Wait until the provider list reflects the key that was just saved.
    const landed = providers.some((p) => p.secret_keys?.includes(savedSlot) && slotEffective(p));
    if (!landed) return;
    const startable = startableProviders(providers);
    const plan = plans.find((p) => p.key_slots.some((s) => s.slot === savedSlot) && planKeysComplete(p, startable));
    let cancelled = false;
    setConnecting(true);
    void (async () => {
      try {
        if (plan) {
          await selectStarterPlan(plan.id).catch(() => undefined);
          const outcome = await applyStarterPlan(plan);
          if (cancelled) return;
          if (outcome.failed.length > 0) setPartial(outcome.failed.map((f) => f.surface).join(", "));
          setConnected(plan.label);
        } else {
          const brain = startable.find((p) => p.secret_keys.includes(savedSlot) && slotEffective(p));
          if (brain && !startable.some((p) => p.active)) await switchBrainProvider(brain.id);
          if (cancelled) return;
          setConnected(brain?.label ?? withKey.find((p) => p.secret_keys.includes(savedSlot))?.label ?? savedSlot);
        }
        cheer();
      } catch (e) {
        if (!cancelled) setPartial(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setConnecting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedSlot, providers, plans]);

  return (
    <div className="space-y-3">
      {connecting ? (
        <Status tone="muted" testId="setup-keys-connecting">{t("first_run.keys.connecting")}</Status>
      ) : connected ? (
        <Status tone="ok" testId="setup-keys-connected">{fill(t("first_run.keys.connected"), { provider: connected })}</Status>
      ) : hasKey ? (
        <Status tone="ok" testId="setup-keys-present">{t("first_run.keys.present")}</Status>
      ) : (
        <Status tone="muted" testId="setup-keys-waiting">{t("first_run.keys.waiting")}</Status>
      )}
      {partial && <Status tone="warning">{fill(t("first_run.keys.partial"), { parts: partial })}</Status>}
      <p className="flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
        <Lock aria-hidden className="mt-0.5 h-3 w-3 shrink-0" />
        {t("first_run.keys.security")}
      </p>
      <PrimaryAction onClick={next} disabled={!hasKey || connecting}>
        {t("first_run.continue")}
      </PrimaryAction>
      {!hasKey && (
        <div className="text-center">
          <QuietAction onClick={later} testId="setup-keys-later" className="text-xs">
            {t("first_run.keys.later")}
          </QuietAction>
        </div>
      )}
    </div>
  );
}

/** The wake-word group of Settings is open behind the card; the step only says what is set. */
function VoiceStep({ next }: { next: () => void }) {
  const t = useT();
  const { config } = useWakeWord();
  const on = Boolean(config?.enabled && config.phrase.trim());
  return (
    <div className="space-y-3">
      <Status tone={on ? "ok" : "muted"} testId="setup-voice-status">
        {on ? fill(t("first_run.voice.on"), { phrase: config!.phrase }) : t("first_run.voice.off")}
      </Status>
      <PrimaryAction onClick={next}>{t("first_run.continue")}</PrimaryAction>
    </div>
  );
}

/** macOS only: the permission rows of Settings are open behind the card. */
function PermissionsStep({ next }: { next: () => void }) {
  const t = useT();
  return (
    <div className="space-y-3">
      <p className="text-xs leading-relaxed text-muted-foreground">{t("first_run.permissions.note")}</p>
      <PrimaryAction onClick={next}>{t("first_run.continue")}</PrimaryAction>
    </div>
  );
}

function ReviewRow({ label, value, ok }: { label: string; value: ReactNode; ok: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border px-3.5 py-2.5 last:border-b-0">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className={cn("flex min-w-0 items-center gap-2 text-right text-sm", ok ? "font-medium text-foreground" : "text-muted-foreground")}>
        <span className="truncate">{value}</span>
        <span aria-hidden className={cn("h-1.5 w-1.5 shrink-0 rounded-full", ok ? "bg-success" : "bg-border-strong")} />
      </dd>
    </div>
  );
}

/**
 * What is set up, read back from the app itself, then the start. Completing
 * restarts the app once so every choice takes effect together; the tour of
 * the app follows the restart.
 */
function ReadyStep({ onb, preview, onFinished }: { onb: Onb; preview: boolean; onFinished: () => void }) {
  const t = useT();
  const { providers } = useProviders();
  const { config } = useWakeWord();
  const [autostart, setAutostart] = useState<{ enabled: boolean; supported: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch("/api/settings/autostart");
        if (res.ok && !cancelled) setAutostart((await res.json()) as { enabled: boolean; supported: boolean });
      } catch {
        // Best-effort; without the probe the switch is not shown.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function toggleAutostart(enabled: boolean) {
    setAutostart((s) => (s ? { ...s, enabled } : s));
    if (preview) return;
    try {
      await fetch("/api/settings/autostart", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
    } catch {
      // The optimistic value stays; Settings is where it can be fixed.
    }
  }

  async function start() {
    if (busy) return;
    if (preview) {
      onFinished();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onb.complete();
    } catch {
      setError(t("first_run.ready.start_failed"));
      setBusy(false);
    }
  }

  const brain = providers.find((p) => p.tier === "brain" && p.active);
  const brainOk = Boolean(brain && ((brain.secret_keys?.length ?? 0) === 0 || slotEffective(brain)));
  const wakeOn = Boolean(config?.enabled && config.phrase.trim());

  return (
    <div className="space-y-3">
      <dl className="overflow-hidden rounded-xl border border-border bg-background" data-testid="onboarding-review">
        <ReviewRow label={t("first_run.ready.row_brain")} value={brainOk ? brain!.label : t("first_run.ready.no_key")} ok={brainOk} />
        <ReviewRow
          label={t("first_run.ready.row_voice")}
          value={wakeOn ? config!.phrase : t("first_run.ready.shortcut")}
          ok={wakeOn}
        />
        {autostart?.supported && (
          <div className="flex items-center justify-between gap-3 px-3.5 py-2.5">
            <span className="text-sm text-muted-foreground">{t("first_run.ready.autostart")}</span>
            <Switch
              checked={autostart.enabled}
              onCheckedChange={(v) => void toggleAutostart(v)}
              aria-label={t("first_run.ready.autostart")}
              data-testid="onboarding-autostart"
            />
          </div>
        )}
      </dl>
      {!brainOk && <Status tone="muted">{t("first_run.ready.no_key_note")}</Status>}
      <p className="text-xs leading-relaxed text-muted-foreground">
        {preview ? t("first_run.ready.preview_note") : t("first_run.ready.restart_note")}
      </p>
      {error && <Status tone="error">{error}</Status>}
      <PrimaryAction onClick={() => void start()} busy={busy} testId="onboarding-start">
        {busy ? t("first_run.ready.starting") : t("first_run.ready.start")}
      </PrimaryAction>
    </div>
  );
}
