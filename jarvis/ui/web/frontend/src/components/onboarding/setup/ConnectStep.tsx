/**
 * Setup window, step 2: connect an AI.
 *
 * One row per provider. A row offers both ways in side by side: sign in with
 * a subscription the user already pays for (the official CLI opens the
 * browser, and the row polls until the login lands), or paste an API key
 * right in the row. Providers that only take a key sit under "More
 * providers". A key saved here is switched on right away (a starter plan it
 * completes, or the Brain when none is active yet).
 *
 * Which key card belongs to a row is decided by provider FAMILY from the
 * catalog, never a hardcoded id, so any single key that can be the brain
 * works (AP-21).
 */
import { Check, ChevronRight, Copy, Loader2, Lock } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiKeyForm } from "@/components/ApiKeyForm";
import { providerFamily, ProviderLogo } from "@/components/providers/ProviderLogo";
import { Button } from "@/components/ui/button";
import { switchBrainProvider, useProviders, type ProviderDescriptor } from "@/hooks/useProviders";
import { applyStarterPlan, getStarterPlans, selectStarterPlan, type StarterPlan } from "@/hooks/useStarterPlans";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { planKeysComplete, primarySlot, slotConfigured, slotEffective, startableProviders } from "../brainPlans";

/** How a subscription reads its CLI login, and when it counts as signed in. */
export interface SubscriptionSpec {
  statusUrl: string;
  loginUrl: string;
  /** The plans that sign in here — product names, the same in every language. */
  plans: string;
  isReady: (status: CliStatus) => boolean;
}

export interface CliStatus {
  installed?: boolean;
  connected?: boolean;
  mode?: string;
  user_email?: string | null;
}

/** A provider row: its brand, its subscription (if any) and the key family it takes. */
export interface ProviderRowSpec {
  id: string;
  label: string;
  /** A provider id ProviderLogo knows, for the brand mark. */
  logoId: string;
  /** The `providerFamily` of the brain key card this row edits. */
  keyFamily: string;
  subscription: SubscriptionSpec;
}

export const PROVIDER_ROWS: readonly ProviderRowSpec[] = [
  {
    id: "claude",
    label: "Claude",
    logoId: "claude-cli",
    keyFamily: "claude",
    subscription: {
      statusUrl: "/api/claude/status",
      loginUrl: "/api/claude/login",
      plans: "Claude Pro · Max",
      isReady: (s) => Boolean(s.connected && s.mode === "subscription"),
    },
  },
  {
    id: "openai",
    label: "OpenAI",
    logoId: "openai-codex",
    keyFamily: "openai",
    subscription: {
      statusUrl: "/api/codex/status",
      loginUrl: "/api/codex/login",
      plans: "ChatGPT Plus · Pro",
      isReady: (s) => Boolean(s.connected),
    },
  },
  {
    id: "google",
    label: "Google Gemini",
    logoId: "gemini",
    keyFamily: "gemini",
    subscription: {
      statusUrl: "/api/antigravity/status",
      loginUrl: "/api/antigravity/login",
      plans: "Google AI Pro · Ultra",
      isReady: (s) => Boolean(s.connected && s.mode !== "api_key"),
    },
  },
  {
    id: "grok",
    label: "xAI Grok",
    logoId: "grok",
    keyFamily: "xai",
    subscription: {
      statusUrl: "/api/grok-build/status",
      loginUrl: "/api/grok-build/login",
      plans: "SuperGrok · X Premium+",
      isReady: (s) => Boolean(s.connected && s.mode === "subscription"),
    },
  },
];

/** How often a row re-reads its login while the browser sign-in runs. */
const LOGIN_POLL_MS = 2500;
/** Give up waiting after this long; the row offers Sign in again. */
const LOGIN_POLL_MAX_MS = 180_000;

async function readStatus(url: string): Promise<CliStatus | null> {
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return (await res.json()) as CliStatus;
  } catch {
    // A warming backend reads as "not known yet"; the row offers Sign in.
    return null;
  }
}

type LoginPhase = "checking" | "ready" | "idle" | "starting" | "waiting";

interface SubscriptionState {
  phase: LoginPhase;
  status: CliStatus | null;
  error: string | null;
  installCommand: string | null;
  connect: () => Promise<void>;
  checkAgain: () => Promise<void>;
}

/** One subscription's login: read once, then poll only while a sign-in runs. */
function useSubscription(spec: SubscriptionSpec): SubscriptionState {
  const [phase, setPhase] = useState<LoginPhase>("checking");
  const [status, setStatus] = useState<CliStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [installCommand, setInstallCommand] = useState<string | null>(null);
  const alive = useRef(true);

  const apply = useCallback(
    (next: CliStatus | null) => {
      if (!alive.current) return false;
      setStatus(next);
      return Boolean(next && spec.isReady(next));
    },
    [spec],
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

  const connect = useCallback(async () => {
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
  }, [spec, apply]);

  const checkAgain = useCallback(async () => {
    setPhase("checking");
    const fresh = await readStatus(spec.statusUrl);
    const ready = apply(fresh);
    if (!alive.current) return;
    if (ready || fresh?.installed) setInstallCommand(null);
    setPhase(ready ? "ready" : "idle");
  }, [spec.statusUrl, apply]);

  return { phase, status, error, installCommand, connect, checkAgain };
}

/** The brand mark without a tile, beside the row's name. */
function Mark({ providerId, label }: { providerId: string; label: string }) {
  return <ProviderLogo providerId={providerId} label={label} className="h-5 w-5 rounded-none bg-transparent" />;
}

function ReadyMark() {
  const t = useT();
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-success">
      <Check aria-hidden className="h-3.5 w-3.5" />
      {t("first_run.connect.ready")}
    </span>
  );
}

/** The key field for one brain key card, opened inside its row. */
function KeyPanel({ provider, onChanged }: { provider: ProviderDescriptor; onChanged: () => void }) {
  const slot = primarySlot(provider);
  if (!slot) return null;
  return (
    <div className="border-t border-border px-4 pb-4 pt-3" data-testid={`setup-key-panel-${provider.id}`}>
      <ApiKeyForm
        secretKey={slot}
        dashboardUrl={provider.dashboard_url}
        configured={slotConfigured(provider)}
        effectiveConfigured={slotEffective(provider)}
        credentialHelp={provider.credential_help}
        onChanged={onChanged}
      />
    </div>
  );
}

function KeyToggle({ open, onClick, testId }: { open: boolean; onClick: () => void; testId: string }) {
  const t = useT();
  return (
    <Button size="sm" variant="ghost" onClick={onClick} aria-expanded={open} data-testid={testId} className="text-muted-foreground">
      {t("first_run.connect.api_key")}
      <ChevronRight aria-hidden className={cn("transition-transform", open && "rotate-90")} />
    </Button>
  );
}

/** A provider with both ways in: a subscription sign-in and an API key. */
function ProviderRow({
  spec,
  keyProvider,
  onReady,
  onKeyChanged,
}: {
  spec: ProviderRowSpec;
  keyProvider: ProviderDescriptor | undefined;
  onReady: (id: string, ready: boolean) => void;
  onKeyChanged: () => void;
}) {
  const t = useT();
  const sub = useSubscription(spec.subscription);
  const [keyOpen, setKeyOpen] = useState(false);
  const signedIn = sub.phase === "ready";
  const keySaved = Boolean(keyProvider && slotEffective(keyProvider));

  useEffect(() => {
    onReady(spec.id, signedIn);
  }, [spec.id, signedIn, onReady]);

  const parts: string[] = [];
  if (signedIn) {
    parts.push(
      sub.status?.user_email
        ? fill(t("first_run.connect.signed_in_as"), { account: sub.status.user_email })
        : t("first_run.connect.signed_in"),
    );
  }
  if (keySaved) parts.push(t("first_run.connect.key_saved"));
  const detail =
    sub.phase === "waiting"
      ? t("first_run.connect.finish_in_browser")
      : parts.length > 0
        ? parts.join(" · ")
        : keyProvider
          ? fill(t("first_run.connect.plans_or_key"), { plans: spec.subscription.plans })
          : spec.subscription.plans;

  return (
    <div className="rounded-lg border border-border bg-popover" data-testid={`setup-sub-${spec.id}`} data-state={sub.phase}>
      <div className="flex flex-wrap items-center gap-3 px-4 py-3.5">
        <Mark providerId={spec.logoId} label={spec.label} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">{spec.label}</p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">{detail}</p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {keyProvider && (
            <KeyToggle open={keyOpen} onClick={() => setKeyOpen((o) => !o)} testId={`setup-sub-${spec.id}-key`} />
          )}
          {signedIn ? (
            <ReadyMark />
          ) : sub.phase === "checking" ? (
            <span className="px-2 text-xs text-muted-foreground">{t("first_run.connect.checking")}</span>
          ) : sub.phase === "waiting" ? (
            <span className="inline-flex items-center gap-1.5 px-2 text-xs text-muted-foreground">
              <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
              {t("first_run.connect.waiting")}
            </span>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={() => void sub.connect()}
              disabled={sub.phase === "starting"}
              data-testid={`setup-sub-${spec.id}-connect`}
            >
              {sub.phase === "starting" && <Loader2 aria-hidden className="animate-spin" />}
              {t("first_run.connect.sign_in")}
            </Button>
          )}
        </div>
      </div>
      {sub.installCommand && !signedIn && (
        <div className="space-y-2 px-4 pb-3.5 pl-12" data-testid={`setup-sub-${spec.id}-install`}>
          <p className="text-xs text-muted-foreground">{t("first_run.connect.install_first")}</p>
          <CommandBlock command={sub.installCommand} />
          <Button size="sm" variant="ghost" onClick={() => void sub.checkAgain()}>
            {t("first_run.connect.check_again")}
          </Button>
        </div>
      )}
      {sub.error && !signedIn && <p className="px-4 pb-3 pl-12 text-xs text-destructive">{sub.error}</p>}
      {keyOpen && keyProvider && <KeyPanel provider={keyProvider} onChanged={onKeyChanged} />}
    </div>
  );
}

/** A provider that only takes an API key. */
function KeyOnlyRow({ provider, onKeyChanged }: { provider: ProviderDescriptor; onKeyChanged: () => void }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const saved = slotEffective(provider);
  return (
    <div className="rounded-lg border border-border bg-popover" data-testid={`setup-key-row-${provider.id}`}>
      <div className="flex items-center gap-3 px-4 py-3.5">
        <Mark providerId={provider.id} label={provider.label} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">{provider.label}</p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {saved ? t("first_run.connect.key_saved") : t("first_run.connect.key_only")}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <KeyToggle open={open} onClick={() => setOpen((o) => !o)} testId={`setup-key-row-${provider.id}-key`} />
          {saved && <ReadyMark />}
        </div>
      </div>
      {open && <KeyPanel provider={provider} onChanged={onKeyChanged} />}
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
  const hasKey = startableNow.some(slotEffective) || localBrain;

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

/**
 * The step's body. `onConnectedChange` reports whether anything is connected
 * (a subscription or a key), so the window can say what Continue leaves out.
 */
export function ConnectStep({ onConnectedChange }: { onConnectedChange: (any: boolean) => void }) {
  const t = useT();
  const { providers, refetch } = useProviders();
  const activation = useKeyActivation(providers);
  const startable = useMemo(() => startableProviders(providers), [providers]);
  const [ready, setReady] = useState<Record<string, boolean>>({});
  const [moreOpen, setMoreOpen] = useState(false);
  const onReady = useCallback((id: string, ok: boolean) => {
    setReady((prev) => (prev[id] === ok ? prev : { ...prev, [id]: ok }));
  }, []);
  const onKeyChanged = useCallback(() => void refetch(), [refetch]);
  const anything = Object.values(ready).some(Boolean) || activation.hasKey;

  useEffect(() => {
    onConnectedChange(anything);
  }, [anything, onConnectedChange]);

  const rowFamilies = new Set(PROVIDER_ROWS.map((r) => r.keyFamily));
  const keyFor = (family: string) => startable.find((p) => providerFamily(p.id) === family);
  const others = startable.filter((p) => !rowFamilies.has(providerFamily(p.id) ?? ""));
  const { connecting, connected, partial, liveVoice, hasKey } = activation;

  return (
    <div className="space-y-1.5">
      {PROVIDER_ROWS.map((spec) => (
        <ProviderRow
          key={spec.id}
          spec={spec}
          keyProvider={keyFor(spec.keyFamily)}
          onReady={onReady}
          onKeyChanged={onKeyChanged}
        />
      ))}
      {others.length > 0 && (
        <div className="pt-1">
          <button
            type="button"
            onClick={() => setMoreOpen((o) => !o)}
            aria-expanded={moreOpen}
            data-testid="setup-more-providers"
            className="group flex items-center gap-1 rounded text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ChevronRight aria-hidden className={cn("h-3.5 w-3.5 transition-transform", moreOpen && "rotate-90")} />
            {t("first_run.connect.more")}
          </button>
          {moreOpen && (
            <div className="mt-2 space-y-1.5">
              {others.map((p) => (
                <KeyOnlyRow key={p.id} provider={p} onKeyChanged={onKeyChanged} />
              ))}
            </div>
          )}
        </div>
      )}
      <div className="space-y-1.5 pt-3 text-xs leading-relaxed text-muted-foreground" data-testid="setup-key-status">
        {connecting ? (
          <p>{t("first_run.connect.key_connecting")}</p>
        ) : connected ? (
          <p>{fill(t("first_run.connect.key_connected"), { provider: connected })}</p>
        ) : null}
        {partial && <p className="text-warning">{fill(t("first_run.connect.key_partial"), { parts: partial })}</p>}
        {hasKey && !connecting && (
          <p data-testid="setup-key-live">{liveVoice ? t("first_run.connect.live_ready") : t("first_run.connect.live_missing")}</p>
        )}
        <p className="flex items-start gap-1.5">
          <Lock aria-hidden className="mt-0.5 h-3 w-3 shrink-0" />
          {t("first_run.connect.key_security")}
        </p>
      </div>
    </div>
  );
}
