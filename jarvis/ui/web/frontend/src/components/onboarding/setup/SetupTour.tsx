import { ArrowLeft, Lock } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Switch } from "@/components/ui/switch";
import type { useOnboarding } from "@/hooks/useOnboarding";
import { switchBrainProvider, useProviders } from "@/hooks/useProviders";
import { applyStarterPlan, getStarterPlans, selectStarterPlan, type StarterPlan } from "@/hooks/useStarterPlans";
import { useWakeWord } from "@/hooks/useWakeWord";
import { fetchAgentConnections } from "@/lib/agentChatApi";
import { clearApiKeysTabRequest, requestApiKeysTab } from "@/lib/apiKeysTab";
import { fill, setUiLanguage, useLocaleChunk, useT, useUiLanguage, type UiLanguage } from "@/i18n";
import type { PetState } from "@/lib/petStates";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { planKeysComplete, slotEffective, startableProviders } from "../brainPlans";
import { ProgressDots } from "../ProgressDots";
import { Spotlight } from "../tour/Spotlight";
import { PrimaryAction, QuietAction, Status } from "../ui";
import { resumeStep, SETUP_STEPS, stepsFor, type SetupStepId } from "./setupSteps";
import { GuidePetFigure } from "../pet/GuidePet";
import { HowWalk } from "./HowWalk";
import { useAnchorRect } from "./useAnchorRect";

type Onb = ReturnType<typeof useOnboarding>;

/** How the guiding pet looks on each step's card. */
const PET: Record<SetupStepId, PetState> = {
  welcome: "success",
  how: "talking",
  keys: "thinking",
  subscriptions: "thinking",
  voice: "listening",
  permissions: "idle",
  ready: "success",
};

const LANGS: UiLanguage[] = ["en", "de", "es"];

/**
 * First-run setup, done inside the real app.
 *
 * There is no setup screen of its own: the window dims, and the guide walks
 * the user to the places where each thing is really set — the API Keys page
 * for one key, its Agents tab for a subscription, the wake-word group in
 * Settings, on macOS the permissions —
 * and waits there with a small card. The dim takes clicks, the hole does
 * not: only the part being set up can be used, so nothing else starts before
 * setup is done. Every step has a way on without doing it. There is no
 * consent gate: it is an open-source app, the welcome only picks a language.
 *
 * The last step hands over to the tour of the app straight away; the gate
 * completes onboarding (and restarts the app once) when the tour ends.
 * `preview` (a replay) never writes — it only walks the steps and hands over
 * to the tour. `startAt` lets a replay from Settings begin at the API Keys.
 */
export function SetupTour({
  onb,
  preview,
  onFinished,
  onSkipAll,
  startAt,
}: {
  onb: Onb;
  preview: boolean;
  onFinished: () => void;
  /** "Skip setup" on the welcome: ends the whole first-run guide. */
  onSkipAll?: () => void;
  startAt?: SetupStepId;
}) {
  const t = useT();
  const ready = useLocaleChunk("onboarding");
  const [platform, setPlatform] = useState<string | null>(null);
  const steps = useMemo(() => stepsFor(platform), [platform]);
  // A replay shows every step from the start; a real first run resumes where
  // it left off (never past the consent).
  const [stepId, setStepId] = useState<SetupStepId>(() =>
    preview
      ? (startAt ?? "welcome")
      : resumeStep(stepsFor(null), onb.state?.current_step ?? null),
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

  // Open the app's own place for this step before pointing at it — on the
  // API Keys page also the right tab.
  useEffect(() => {
    if (!ready || !step.section) return;
    if (step.section === "apikeys") requestApiKeysTab(step.apiKeysTab ?? null);
    else clearApiKeysTabRequest();
    const nav = useEventStore.getState();
    if (nav.activeSection !== step.section) nav.setActiveSection(step.section);
  }, [ready, step.section, step.apiKeysTab, stepId]);

  // Keep the app on this step's place. The dim blocks every click, so a
  // drift is something else moving the app (a section restored late in
  // boot) — the card would then describe a page that is not on screen.
  useEffect(() => {
    if (!ready || !step.section) return;
    const target = step.section;
    return useEventStore.subscribe((state) => {
      if (state.activeSection !== target) state.setActiveSection(target);
    });
  }, [ready, step.section]);

  // Later visits to the API Keys page open on its own default tab again.
  useEffect(() => clearApiKeysTabRequest, []);

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
  // Never back to the welcome, nor behind where a replay started.
  const firstId = preview && startAt ? startAt : steps[1];
  const prevId = index > 1 && stepId !== firstId ? steps[index - 1] : null;

  const next = useCallback(() => {
    if (nextId) goTo(nextId, skipped);
  }, [nextId, goTo, skipped]);

  const later = useCallback(() => {
    const nextSkipped = skipped.includes(stepId) ? skipped : [...skipped, stepId];
    setSkipped(nextSkipped);
    if (nextId) goTo(nextId, nextSkipped);
  }, [skipped, stepId, nextId, goTo]);

  if (!ready) return null;

  // The explanation is its own walk through the real app, told by the pet.
  if (stepId === "how") return <HowWalk onDone={() => { cheer(); next(); }} />;

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
        className="max-h-[calc(100vh-24px)] overflow-y-auto rounded-2xl border border-border bg-popover p-5 text-popover-foreground shadow-float"
        data-testid="setup-card"
        data-step={stepId}
      >
        <div className="flex items-start gap-3">
          <GuidePetFigure key={cue} state={PET[stepId]} px={48} />
          <div className="min-w-0 flex-1">
            <h2 id="setup-title" className="text-base font-semibold tracking-tight text-foreground">
              {t(`first_run.${stepId}.title`)}
            </h2>
            <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{t(`first_run.${stepId}.lede`)}</p>
          </div>
        </div>
        <div className="mt-4">
          {stepId === "welcome" && <WelcomeStep onStart={() => { cheer(); next(); }} onSkipAll={onSkipAll} />}
          {stepId === "keys" && <KeysStep next={next} later={later} cheer={cheer} />}
          {stepId === "subscriptions" && <SubscriptionsStep next={next} later={later} />}
          {stepId === "voice" && <VoiceStep next={next} later={later} />}
          {stepId === "permissions" && <PermissionsStep next={next} />}
          {stepId === "ready" && <ReadyStep preview={preview} onFinished={onFinished} />}
        </div>
        {footer}
      </div>
    </Spotlight>
  );
}

/* ------------------------------------------------------------------ steps */

function WelcomeStep({ onStart, onSkipAll }: { onStart: () => void; onSkipAll?: () => void }) {
  const t = useT();
  const lang = useUiLanguage();
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
      <PrimaryAction onClick={onStart}>{t("first_run.welcome.start")}</PrimaryAction>
      {onSkipAll && (
        <div className="text-center">
          <QuietAction onClick={onSkipAll} testId="setup-skip-all" className="text-xs">
            {t("first_run.welcome.skip_all")}
          </QuietAction>
        </div>
      )}
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
  // Live voice (talk and get an instant answer) needs a key one of the
  // realtime starter plans runs on; any other key still runs chat and the
  // classic voice (speech to text, answer read aloud).
  const startableNow = startableProviders(providers);
  const liveVoice = plans.some((p) => p.mode === "realtime" && planKeysComplete(p, startableNow));
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
      {hasKey && !connecting && (
        <Status tone={liveVoice ? "ok" : "muted"} testId="setup-keys-live">
          {liveVoice ? t("first_run.keys.live_ready") : t("first_run.keys.live_missing")}
        </Status>
      )}
      {!hasKey && (
        <p className="text-xs leading-relaxed text-muted-foreground" data-testid="setup-keys-nokey">
          {t("first_run.keys.no_key")}
        </p>
      )}
      {hasKey && !connecting && (
        <p className="text-xs leading-relaxed text-muted-foreground">{t("first_run.keys.next_hint")}</p>
      )}
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

/** Poll interval while the subscriptions step waits for a sign-in. */
const SUBSCRIPTION_POLL_MS = 3000;

/**
 * The Agents tab of the API Keys page is open behind the card. Agents run
 * best on a subscription (Claude, ChatGPT/Codex, …): a flat monthly price
 * instead of paying per call. The step waits for one to be signed in; the
 * sign-in itself happens on the tab.
 */
function SubscriptionsStep({ next, later }: { next: () => void; later: () => void }) {
  const t = useT();
  const connected = useConnectedSubscriptions(SUBSCRIPTION_POLL_MS);
  const has = connected.length > 0;
  return (
    <div className="space-y-3">
      <Status tone={has ? "ok" : "muted"} testId="setup-subscriptions-status">
        {has
          ? fill(t("first_run.subscriptions.connected"), { names: connected.join(", ") })
          : t("first_run.subscriptions.waiting")}
      </Status>
      <p className="text-xs leading-relaxed text-muted-foreground">{t("first_run.subscriptions.why")}</p>
      <PrimaryAction onClick={next} disabled={!has}>
        {t("first_run.continue")}
      </PrimaryAction>
      {!has && (
        <div className="text-center">
          <QuietAction onClick={later} testId="setup-subscriptions-later" className="text-xs">
            {t("first_run.subscriptions.later")}
          </QuietAction>
        </div>
      )}
    </div>
  );
}

/**
 * Subscription names for the status rows whose own label names the API side
 * ("Claude (API-Key)") or is missing; anything else shows its own label.
 */
const SUBSCRIPTION_NAMES: Record<string, string> = {
  "claude-api": "Claude",
  "openai-codex": "ChatGPT (Codex)",
  antigravity: "Antigravity",
  "grok-build": "Grok Build",
};

/**
 * Names of the agent providers signed in with a subscription. `pollMs`
 * re-reads while a step waits for a sign-in; 0 reads once.
 */
function useConnectedSubscriptions(pollMs: number): string[] {
  const [names, setNames] = useState<string[]>([]);
  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    const read = async () => {
      try {
        const rows = await fetchAgentConnections();
        if (!cancelled) {
          const found = rows.filter((r) => r.oauth_connected).map((r) => SUBSCRIPTION_NAMES[r.jarvis] ?? (r.label || r.jarvis));
          setNames((prev) => (prev.join("|") === found.join("|") ? prev : found));
        }
      } catch {
        // Best-effort: a warming backend just reads as "nothing connected yet".
      }
      if (!cancelled && pollMs > 0) timer = window.setTimeout(() => void read(), pollMs);
    };
    void read();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [pollMs]);
  return names;
}

/**
 * The wake-word group of Settings is open behind the card. Continue waits
 * for a saved wake word; without one the Call shortcut is the way in, so the
 * step can still be left for later.
 */
function VoiceStep({ next, later }: { next: () => void; later: () => void }) {
  const t = useT();
  const { config } = useWakeWord();
  const on = Boolean(config?.enabled && config.phrase.trim());
  return (
    <div className="space-y-3">
      <Status tone={on ? "ok" : "muted"} testId="setup-voice-status">
        {on ? fill(t("first_run.voice.on"), { phrase: config!.phrase }) : t("first_run.voice.off")}
      </Status>
      <PrimaryAction onClick={next} disabled={!on}>
        {t("first_run.continue")}
      </PrimaryAction>
      {!on && (
        <div className="text-center">
          <QuietAction onClick={later} testId="setup-voice-later" className="text-xs">
            {t("first_run.voice.later")}
          </QuietAction>
        </div>
      )}
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
 * What is set up, read back from the app itself, then on to the tour. The
 * one restart that switches every choice on comes when the tour ends.
 */
function ReadyStep({ preview, onFinished }: { preview: boolean; onFinished: () => void }) {
  const t = useT();
  const { providers } = useProviders();
  const { config } = useWakeWord();
  const subscriptions = useConnectedSubscriptions(0);
  const [autostart, setAutostart] = useState<{ enabled: boolean; supported: boolean } | null>(null);

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

  const brain = providers.find((p) => p.tier === "brain" && p.active);
  const brainOk = Boolean(brain && ((brain.secret_keys?.length ?? 0) === 0 || slotEffective(brain)));
  const wakeOn = Boolean(config?.enabled && config.phrase.trim());

  return (
    <div className="space-y-3">
      <dl className="overflow-hidden rounded-xl border border-border bg-background" data-testid="onboarding-review">
        <ReviewRow label={t("first_run.ready.row_brain")} value={brainOk ? brain!.label : t("first_run.ready.no_key")} ok={brainOk} />
        <ReviewRow
          label={t("first_run.ready.row_agents")}
          value={subscriptions.length > 0 ? subscriptions.join(", ") : t("first_run.ready.no_subscription")}
          ok={subscriptions.length > 0}
        />
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
      <PrimaryAction onClick={onFinished} testId="onboarding-start">
        {t("first_run.ready.start")}
      </PrimaryAction>
    </div>
  );
}
