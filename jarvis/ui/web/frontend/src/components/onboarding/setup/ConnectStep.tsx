/**
 * Setup window, step 2: connect an AI.
 *
 * Subscriptions the user already pays for come first, one row each, signed
 * in with one click — the official CLI opens the browser, and the row polls
 * until the login lands. An API key sits below as its own row: one key is
 * all the assistant needs to think and talk, and a key saved here is switched
 * on right away (a starter plan it completes, or the Brain when none is
 * active yet).
 */
import { Check, ChevronRight, Copy, KeyRound, Loader2, Lock } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiKeyForm } from "@/components/ApiKeyForm";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { Button } from "@/components/ui/button";
import { switchBrainProvider, useProviders, type ProviderDescriptor } from "@/hooks/useProviders";
import { applyStarterPlan, getStarterPlans, selectStarterPlan, type StarterPlan } from "@/hooks/useStarterPlans";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { planKeysComplete, primarySlot, slotConfigured, slotEffective, startableProviders } from "../brainPlans";

/** How a subscription row reads its CLI login, and when it counts as signed in. */
export interface SubscriptionSpec {
  id: string;
  /** A provider id ProviderLogo knows, for the brand mark. */
  logoId: string;
  label: string;
  /** The plans that sign in here — product names, the same in every language. */
  plans: string;
  statusUrl: string;
  loginUrl: string;
  isReady: (status: CliStatus) => boolean;
}

export interface CliStatus {
  installed?: boolean;
  connected?: boolean;
  mode?: string;
  user_email?: string | null;
}

export const SUBSCRIPTIONS: readonly SubscriptionSpec[] = [
  {
    id: "claude",
    logoId: "claude-cli",
    label: "Claude",
    plans: "Claude Pro · Max",
    statusUrl: "/api/claude/status",
    loginUrl: "/api/claude/login",
    isReady: (s) => Boolean(s.connected && s.mode === "subscription"),
  },
  {
    id: "codex",
    logoId: "openai-codex",
    label: "ChatGPT",
    plans: "ChatGPT Plus · Pro (Codex)",
    statusUrl: "/api/codex/status",
    loginUrl: "/api/codex/login",
    isReady: (s) => Boolean(s.connected),
  },
  {
    id: "antigravity",
    logoId: "antigravity",
    label: "Google",
    plans: "Google AI Pro · Ultra (Antigravity)",
    statusUrl: "/api/antigravity/status",
    loginUrl: "/api/antigravity/login",
    isReady: (s) => Boolean(s.connected && s.mode !== "api_key"),
  },
  {
    id: "grok",
    logoId: "grok-build",
    label: "Grok",
    plans: "SuperGrok · X Premium+",
    statusUrl: "/api/grok-build/status",
    loginUrl: "/api/grok-build/login",
    isReady: (s) => Boolean(s.connected && s.mode === "subscription"),
  },
];

/** How often a row re-reads its login while the browser sign-in runs. */
const LOGIN_POLL_MS = 2500;
/** Give up waiting after this long; the row offers Connect again. */
const LOGIN_POLL_MAX_MS = 180_000;

async function readStatus(url: string): Promise<CliStatus | null> {
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return (await res.json()) as CliStatus;
  } catch {
    // A warming backend reads as "not known yet"; the row shows Connect.
    return null;
  }
}

type RowPhase = "checking" | "ready" | "idle" | "starting" | "waiting";

/** Names of the subscriptions signed in right now — read by the setup's other steps. */
export type ConnectedChange = (id: string, ready: boolean) => void;

function SubscriptionRow({ spec, onReady }: { spec: SubscriptionSpec; onReady: ConnectedChange }) {
  const t = useT();
  const [phase, setPhase] = useState<RowPhase>("checking");
  const [status, setStatus] = useState<CliStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [installCommand, setInstallCommand] = useState<string | null>(null);
  const alive = useRef(true);

  const apply = useCallback(
    (next: CliStatus | null) => {
      if (!alive.current) return false;
      setStatus(next);
      const ready = Boolean(next && spec.isReady(next));
      onReady(spec.id, ready);
      return ready;
    },
    [spec, onReady],
  );

  useEffect(() => {
    alive.current = true;
    void readStatus(spec.statusUrl).then((s) => {
      const ready = apply(s);
      if (alive.current) setPhase(ready ? "ready" : "idle");
    });
    return () => {
      alive.current = false;
    };
  }, [spec.statusUrl, apply]);

  async function connect() {
    setError(null);
    setPhase("starting");
    try {
      const res = await fetch(spec.loginUrl, { method: "POST" });
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
      if (!res.ok) {
        const detail = body.detail;
        if (detail && typeof detail === "object" && "install_command" in detail) {
          setInstallCommand(String((detail as { install_command: unknown }).install_command));
        } else {
          setError(typeof detail === "string" ? detail : `HTTP ${res.status}`);
        }
        if (alive.current) setPhase("idle");
        return;
      }
    } catch (e) {
      setError((e as Error).message);
      if (alive.current) setPhase("idle");
      return;
    }
    setInstallCommand(null);
    setPhase("waiting");
    // The sign-in finishes in the browser the CLI opened; poll until it lands.
    const deadline = Date.now() + LOGIN_POLL_MAX_MS;
    while (alive.current && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, LOGIN_POLL_MS));
      if (apply(await readStatus(spec.statusUrl))) {
        setPhase("ready");
        return;
      }
    }
    if (alive.current) setPhase("idle");
  }

  async function checkAgain() {
    setPhase("checking");
    const fresh = await readStatus(spec.statusUrl);
    const ready = apply(fresh);
    if (!alive.current) return;
    if (ready || fresh?.installed) setInstallCommand(null);
    setPhase(ready ? "ready" : "idle");
  }

  const ready = phase === "ready";
  const detail = ready
    ? status?.user_email
      ? fill(t("first_run.connect.signed_in_as"), { account: status.user_email })
      : t("first_run.connect.signed_in")
    : phase === "waiting"
      ? t("first_run.connect.finish_in_browser")
      : status && status.installed === false
        ? t("first_run.connect.not_installed")
        : spec.plans;

  return (
    <div className="rounded-lg border border-border bg-popover px-4 py-3" data-testid={`setup-sub-${spec.id}`} data-state={phase}>
      <div className="flex items-center gap-3">
        <ProviderLogo providerId={spec.logoId} label={spec.label} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">{spec.label}</p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">{detail}</p>
        </div>
        <div className="shrink-0">
          {ready ? (
            <span className="inline-flex items-center gap-1.5 text-xs font-medium text-success">
              <Check aria-hidden className="h-3.5 w-3.5" />
              {t("first_run.connect.ready")}
            </span>
          ) : phase === "checking" ? (
            <span className="text-xs text-muted-foreground">{t("first_run.connect.checking")}</span>
          ) : phase === "waiting" ? (
            <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
              {t("first_run.connect.waiting")}
            </span>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={() => void connect()}
              disabled={phase === "starting"}
              data-testid={`setup-sub-${spec.id}-connect`}
            >
              {phase === "starting" && <Loader2 aria-hidden className="animate-spin" />}
              {t("first_run.connect.connect")}
            </Button>
          )}
        </div>
      </div>
      {installCommand && !ready && (
        <div className="ml-12 mt-3 space-y-2" data-testid={`setup-sub-${spec.id}-install`}>
          <p className="text-xs text-muted-foreground">{t("first_run.connect.install_first")}</p>
          <CommandBlock command={installCommand} />
          <Button size="sm" variant="ghost" onClick={() => void checkAgain()}>
            {t("first_run.connect.check_again")}
          </Button>
        </div>
      )}
      {error && !ready && <p className="ml-12 mt-2 text-xs text-destructive">{error}</p>}
    </div>
  );
}

function CommandBlock({ command }: { command: string }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // No clipboard access: the command stays selectable on screen.
    }
  }
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-secondary px-3 py-2 font-mono text-xs">
      <span className="min-w-0 select-all truncate">
        <span className="mr-2 text-muted-foreground">$</span>
        {command}
      </span>
      <button
        type="button"
        onClick={() => void copy()}
        aria-label={t("first_run.connect.copy")}
        className="shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-surface-raised hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {copied ? <Check aria-hidden className="h-3.5 w-3.5" /> : <Copy aria-hidden className="h-3.5 w-3.5" />}
      </button>
    </div>
  );
}

export interface KeyActivation {
  /** A key (or a local brain) is there to think with. */
  hasKey: boolean;
  connecting: boolean;
  /** The plan or provider a key saved during setup switched on. */
  connected: string | null;
  partial: string | null;
  /** A saved key runs live voice (a realtime starter plan is complete). */
  liveVoice: boolean;
}

/**
 * A key saved during setup is also switched on: a starter plan that key
 * completes points live voice and its thinking model at it; any other Brain
 * key becomes the Brain when none is active yet. A key that was already
 * there is left exactly as it is.
 */
export function useKeyActivation(providers: ProviderDescriptor[]): KeyActivation {
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

  return { hasKey, connecting, connected, partial, liveVoice };
}

function ApiKeyRow({ activation }: { activation: KeyActivation }) {
  const t = useT();
  const { providers, refetch } = useProviders();
  const startable = useMemo(() => startableProviders(providers), [providers]);
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const current = startable.find((p) => p.id === picked) ?? startable.find(slotEffective) ?? startable[0];
  const keyed = startable.find(slotEffective);
  const slot = current ? primarySlot(current) : null;
  const { hasKey, connecting, connected, partial, liveVoice } = activation;

  const summary = connecting
    ? t("first_run.connect.key_connecting")
    : connected
      ? fill(t("first_run.connect.key_connected"), { provider: connected })
      : keyed
        ? fill(t("first_run.connect.key_present"), { provider: keyed.label })
        : t("first_run.connect.key_hint");

  return (
    <div className="rounded-lg border border-border bg-popover" data-testid="setup-key-row">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        data-testid="setup-key-toggle"
        className="flex w-full items-center gap-3 rounded-lg px-4 py-3 text-left transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-secondary">
          <KeyRound aria-hidden className="h-4 w-4 text-muted-foreground" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-foreground">{t("first_run.connect.key_title")}</span>
          <span className="mt-0.5 block truncate text-xs text-muted-foreground" data-testid="setup-key-summary">
            {summary}
          </span>
        </span>
        {hasKey && !connecting ? (
          <span className="inline-flex shrink-0 items-center gap-1.5 text-xs font-medium text-success">
            <Check aria-hidden className="h-3.5 w-3.5" />
            {t("first_run.connect.ready")}
          </span>
        ) : connecting ? (
          <Loader2 aria-hidden className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" />
        ) : null}
        <ChevronRight
          aria-hidden
          className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")}
        />
      </button>
      {open && (
        <div className="space-y-3 px-4 pb-4" data-testid="setup-key-panel">
          {startable.length > 0 ? (
            <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={t("first_run.connect.key_provider")}>
              {startable.map((p) => {
                const on = p.id === current?.id;
                return (
                  <button
                    key={p.id}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    onClick={() => setPicked(p.id)}
                    data-testid={`setup-key-provider-${p.id}`}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      on
                        ? "border-accent bg-accent-soft font-medium text-foreground"
                        : "border-border text-muted-foreground hover:bg-secondary hover:text-foreground",
                    )}
                  >
                    <ProviderLogo providerId={p.id} label={p.label} size="sm" />
                    {p.label}
                    {slotEffective(p) && <Check aria-hidden className="h-3 w-3 text-success" />}
                  </button>
                );
              })}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">{t("first_run.connect.key_none")}</p>
          )}
          {current && slot && (
            <ApiKeyForm
              key={current.id}
              secretKey={slot}
              dashboardUrl={current.dashboard_url}
              configured={slotConfigured(current)}
              effectiveConfigured={slotEffective(current)}
              credentialHelp={current.credential_help}
              onChanged={() => void refetch()}
            />
          )}
          {partial && (
            <p className="text-xs text-warning">{fill(t("first_run.connect.key_partial"), { parts: partial })}</p>
          )}
          {hasKey && !connecting && (
            <p className="text-xs text-muted-foreground" data-testid="setup-key-live">
              {liveVoice ? t("first_run.connect.live_ready") : t("first_run.connect.live_missing")}
            </p>
          )}
          <p className="flex items-start gap-1.5 text-xs leading-relaxed text-muted-foreground">
            <Lock aria-hidden className="mt-0.5 h-3 w-3 shrink-0" />
            {t("first_run.connect.key_security")}
          </p>
        </div>
      )}
    </div>
  );
}

/**
 * The step's body. `onConnectedChange` reports whether anything is connected
 * (a subscription or a key), so the window can say what Continue leaves out.
 */
export function ConnectStep({ onConnectedChange }: { onConnectedChange: (any: boolean) => void }) {
  const t = useT();
  const { providers } = useProviders();
  const activation = useKeyActivation(providers);
  const [ready, setReady] = useState<Record<string, boolean>>({});
  const onReady = useCallback<ConnectedChange>((id, ok) => {
    setReady((prev) => (prev[id] === ok ? prev : { ...prev, [id]: ok }));
  }, []);
  const anySubscription = Object.values(ready).some(Boolean);
  const anything = anySubscription || activation.hasKey;

  useEffect(() => {
    onConnectedChange(anything);
  }, [anything, onConnectedChange]);

  return (
    <div className="space-y-5">
      <section className="space-y-1.5" aria-labelledby="setup-subs-label">
        <h2 id="setup-subs-label" className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {t("first_run.connect.subscriptions")}
        </h2>
        {SUBSCRIPTIONS.map((spec) => (
          <SubscriptionRow key={spec.id} spec={spec} onReady={onReady} />
        ))}
        <p className="pt-1 text-xs leading-relaxed text-muted-foreground">{t("first_run.connect.subscriptions_why")}</p>
      </section>
      <section className="space-y-1.5" aria-labelledby="setup-key-label">
        <h2 id="setup-key-label" className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {t("first_run.connect.or_key")}
        </h2>
        <ApiKeyRow activation={activation} />
      </section>
    </div>
  );
}
