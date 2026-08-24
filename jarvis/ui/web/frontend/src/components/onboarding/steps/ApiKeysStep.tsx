import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ExternalLink } from "lucide-react";
import { ApiKeyForm } from "@/components/ApiKeyForm";
import { Button, FOCUS_RING } from "@/components/agentic/controls";
import {
  switchBrainProvider,
  useProviders,
  type ProviderDescriptor,
} from "@/hooks/useProviders";
import { setLocalMode } from "@/lib/localMode";
import { putVoiceMode } from "@/lib/voiceEngineMode";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { StepProps } from "../OnboardingFlow";
import { StatusLine, StepFooter, StepSection } from "../primitives";

/** How many providers are open before the "show more" fold. */
const FOLD_AFTER = 4;

/** The one slot a card asks for; providers with several keys expose the first. */
function primarySlot(p: ProviderDescriptor): string | null {
  return p.secret_keys[0] ?? null;
}

function slotConfigured(p: ProviderDescriptor): boolean {
  const slot = primarySlot(p);
  if (!slot) return p.configured;
  return Boolean(p.secrets_set[slot]);
}

function slotEffective(p: ProviderDescriptor): boolean {
  const slot = primarySlot(p);
  if (!slot) return p.configured;
  return Boolean(p.secrets_effective?.[slot] ?? p.secrets_set[slot]);
}

/**
 * The brain providers a first-run user can start with: anything that takes a
 * pasted key and can be the primary brain. Recommended ones first, then
 * whatever already has a key, then the rest in catalog order. Exported for
 * tests.
 */
export function startableProviders(providers: ProviderDescriptor[]): ProviderDescriptor[] {
  return providers
    .filter((p) => p.tier === "brain" && p.auth_mode === "api_key" && p.brain_switchable !== false)
    .filter((p) => (p.secret_keys?.length ?? 0) > 0)
    .sort((a, b) => {
      const ra = a.recommended ? 0 : 1;
      const rb = b.recommended ? 0 : 1;
      if (ra !== rb) return ra - rb;
      const ca = slotConfigured(a) ? 0 : 1;
      const cb = slotConfigured(b) ? 0 : 1;
      return ca - cb;
    });
}

/**
 * First-sixty-seconds local path (maintainer amendment 2026-07-25): a
 * zero-key user with Ollama running must SEE the local option during
 * onboarding, not discover it later in settings. The probe goes through the
 * backend catalog route (never a browser-direct localhost:11434 call, which
 * CORS would block anyway) and runs once on step mount only (AP-26).
 */
function LocalPath({ onActivated }: { onActivated: () => void }) {
  const t = useT();
  const [probe, setProbe] = useState<"checking" | "reachable" | "empty" | "unreachable">(
    "checking",
  );
  const [activating, setActivating] = useState(false);
  const [active, setActive] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/providers/ollama/models");
        const data = res.ok ? await res.json() : null;
        const live = data?.source === "live" || data?.source === "cache";
        const models = Array.isArray(data?.models) ? data.models.length : 0;
        if (!cancelled) {
          setProbe(live ? (models > 0 ? "reachable" : "empty") : "unreachable");
        }
      } catch {
        if (!cancelled) setProbe("unreachable");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function useLocal() {
    setActivating(true);
    setError(null);
    try {
      await switchBrainProvider("ollama");
      // Picking the local brain has to pin the PIPELINE engine as well.
      // Realtime replaces STT + Brain + TTS with one full-duplex cloud model
      // and never consults `[brain].primary`, and `[voice].mode` defaults to
      // realtime — so activating the local brain alone left the user on the
      // realtime cards with their choice having changed nothing audible.
      // Persisted, because onboarding ends in a restart.
      await putVoiceMode("pipeline");
      // Somebody who picks the local path here lands in a provider console
      // whose cards are almost entirely hosted accounts they just declined.
      // Local Mode opens it on the handful that need no key. A view preference,
      // reversible with one click on the switch in that view's header.
      setLocalMode(true);
      setActive(true);
      onActivated();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActivating(false);
    }
  }

  return (
    <StepSection label={t("onboarding.api_keys.local_label")}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-y border-border/70 py-3">
        <div className="min-w-0 space-y-1">
          <p className="text-sm font-medium text-foreground">
            {t("onboarding.api_keys.local_title")}
          </p>
          {probe === "checking" && (
            <p className="text-[13px] text-muted-foreground">
              {t("onboarding.api_keys.local_checking")}
            </p>
          )}
          {probe === "reachable" && !active && (
            <p className="text-[13px] leading-relaxed text-muted-foreground">
              {t("onboarding.api_keys.local_detected")}
            </p>
          )}
          {probe === "reachable" && active && (
            <StatusLine tone="ok" testId="local-active">
              {t("onboarding.api_keys.local_active")}
            </StatusLine>
          )}
          {probe === "empty" && (
            <p className="text-[13px] leading-relaxed text-muted-foreground">
              {t("onboarding.api_keys.local_detected_empty")}
            </p>
          )}
          {probe === "unreachable" && (
            <p className="text-[13px] leading-relaxed text-muted-foreground">
              {t("onboarding.api_keys.local_missing")}{" "}
              <a
                href="https://ollama.com/download"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
              >
                {t("onboarding.api_keys.local_missing_link")}
                <ExternalLink className="h-3 w-3" />
              </a>
            </p>
          )}
          {error && <StatusLine tone="error">{error}</StatusLine>}
        </div>
        {probe === "reachable" && !active && (
          <Button variant="quiet" onClick={() => void useLocal()} disabled={activating}>
            {t("onboarding.api_keys.local_use_button")}
          </Button>
        )}
      </div>
    </StepSection>
  );
}

/**
 * Real key entry on the first run. Each provider is a row of a hairline
 * register — number, name, a "Recommended" tag where the catalog says so, and
 * a status word. Opening a row reveals the same `ApiKeyForm` the API Keys
 * view uses (format recognition, saved state, dashboard link), so what the
 * user learns here is exactly what they will find later. Saving the first
 * key also makes that provider the active brain when none is yet — the
 * point of this step is that chat works the moment the guide ends.
 *
 * The list is driven by the backend catalog, so a catalog with one provider
 * renders one row and nothing here changes.
 */
export function ApiKeysStep({ goNext, goBack, skip, setSummary }: StepProps) {
  const t = useT();
  const { providers, loading, error, refetch } = useProviders();
  const [open, setOpen] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [localActive, setLocalActive] = useState(false);

  const startable = useMemo(() => startableProviders(providers), [providers]);
  const visible = expanded ? startable : startable.slice(0, FOLD_AFTER);
  const hidden = startable.length - visible.length;

  const configured = startable.filter(slotEffective);
  const hasKey = configured.length > 0;

  useEffect(() => {
    if (hasKey) {
      setSummary(configured.map((p) => p.label).join(" · "));
    } else if (localActive) {
      setSummary(t("onboarding.api_keys.summary_local"));
    } else {
      setSummary(null);
    }
    // configured is derived from startable; its labels are what we render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasKey, localActive, configured.map((p) => p.id).join(","), setSummary, t]);

  // Open the first row by default so a fresh install shows an input, not a
  // list of closed doors. Once anything is configured, everything stays shut.
  useEffect(() => {
    if (open === null && startable.length > 0 && !hasKey) setOpen(startable[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startable.length, hasKey]);

  const activateIfNone = (p: ProviderDescriptor) => {
    const anyActive = startable.some((q) => q.active);
    if (anyActive) return;
    void switchBrainProvider(p.id)
      .then(() => refetch())
      .catch(() => {
        // The key is saved either way; the provider console remains the
        // recovery path for choosing the active brain.
      });
  };

  const canContinue = hasKey || localActive;

  return (
    <div className="space-y-8">
      <StepSection label={t("onboarding.api_keys.providers_label")}>
        {loading && startable.length === 0 ? (
          <p className="text-[13px] text-muted-foreground">{t("onboarding.api_keys.loading")}</p>
        ) : error && startable.length === 0 ? (
          <StatusLine tone="warning">{t("onboarding.api_keys.load_failed")}</StatusLine>
        ) : (
          <ol className="border-y border-border/70" data-testid="onboarding-provider-list">
            {visible.map((p, index) => {
              const isOpen = open === p.id;
              const slot = primarySlot(p);
              const saved = slotConfigured(p);
              const effective = slotEffective(p);
              return (
                <li key={p.id} className="border-b border-border/50 last:border-b-0">
                  <button
                    type="button"
                    aria-expanded={isOpen}
                    data-testid={`onboarding-provider-${p.id}`}
                    onClick={() => setOpen(isOpen ? null : p.id)}
                    className={cn(
                      "grid w-full grid-cols-[2rem_minmax(0,1fr)_auto_1rem] items-center gap-2 py-3 text-left",
                      FOCUS_RING,
                    )}
                  >
                    <span className="font-mono text-[10px] tabular-nums text-muted-foreground/70">
                      {(index + 1).toString().padStart(2, "0")}
                    </span>
                    <span className="flex min-w-0 flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
                      <span className="text-sm font-medium text-foreground">{p.label}</span>
                      {p.recommended && (
                        <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-primary">
                          {t("onboarding.api_keys.recommended")}
                        </span>
                      )}
                    </span>
                    <span className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                      <span
                        aria-hidden
                        className={cn(
                          "h-[7px] w-[7px] rounded-full",
                          effective ? "bg-emerald-500" : "bg-muted-foreground/40",
                        )}
                      />
                      {p.active
                        ? t("onboarding.api_keys.active")
                        : effective
                          ? t("onboarding.api_keys.configured")
                          : t("onboarding.api_keys.not_configured")}
                    </span>
                    <ChevronDown
                      aria-hidden
                      className={cn(
                        "h-3.5 w-3.5 text-muted-foreground transition-transform",
                        isOpen && "rotate-180",
                      )}
                    />
                  </button>
                  {isOpen && slot && (
                    <div className="space-y-2 pb-4 pl-10 pr-2">
                      <ApiKeyForm
                        secretKey={slot}
                        dashboardUrl={p.dashboard_url}
                        configured={saved}
                        effectiveConfigured={effective}
                        credentialHelp={p.credential_help}
                        coveredNote={p.credential_note ?? null}
                        sharedWith={p.secret_shared_with?.[slot] ?? []}
                        onChanged={() => void refetch()}
                        onSavedActivate={() => activateIfNone(p)}
                      />
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        )}
        {hidden > 0 && !expanded && (
          <button
            type="button"
            className="text-[13px] text-muted-foreground underline underline-offset-4 hover:text-foreground"
            onClick={() => setExpanded(true)}
          >
            {t("onboarding.api_keys.show_more").replace("{0}", String(hidden))}
          </button>
        )}
        {expanded && startable.length > FOLD_AFTER && (
          <button
            type="button"
            className="text-[13px] text-muted-foreground underline underline-offset-4 hover:text-foreground"
            onClick={() => setExpanded(false)}
          >
            {t("onboarding.api_keys.show_less")}
          </button>
        )}
      </StepSection>

      <LocalPath onActivated={() => setLocalActive(true)} />

      <p className="text-[13px] leading-relaxed text-muted-foreground">
        {t("onboarding.api_keys.security_note")}
      </p>

      <StepFooter
        onBack={goBack}
        primary={{
          label: t("onboarding.nav.next"),
          onClick: goNext,
          disabled: !canContinue,
        }}
        secondary={
          canContinue
            ? null
            : {
                label: t("onboarding.api_keys.later"),
                onClick: skip,
                testId: "onboarding-keys-later",
              }
        }
      />
      {!canContinue && (
        <p className="mt-2 text-right text-[11px] text-muted-foreground">
          {t("onboarding.api_keys.later_hint")}
        </p>
      )}
    </div>
  );
}
