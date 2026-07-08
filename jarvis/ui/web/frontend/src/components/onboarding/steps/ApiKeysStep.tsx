import { useState } from "react";
import { Brain, Check, KeyRound, Mic, Volume2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { ApiKeyForm } from "@/components/ApiKeyForm";
import { AltCredentialNote } from "@/components/AltCredentialNote";
import { ProviderBillingBadge } from "@/components/ProviderBillingBadge";
import { JarvisAgentSection } from "@/components/JarvisAgentSection";
import {
  useProviders,
  switchBrainProvider,
  switchRealtimeProvider,
  switchTtsProvider,
  switchSttProvider,
  type ProviderDescriptor,
  type ProviderTier,
} from "@/hooks/useProviders";
import type { StepProps } from "../OnboardingFlow";

// The provider classes, in the same order as the main Settings → API Keys view
// (Brain first, then Voice, then Hearing). Rendered as stacked sections inside
// one scroll container so the user can scroll through every class at once —
// mirroring the real API-Keys section — instead of paging one class at a time.
// Each provider class gets a plain-language hint so a first-time user
// understands at a glance that Jarvis takes several *kinds* of keys — not just
// one for the brain — and what each kind is for.
const TIERS: { tier: ProviderTier; label: string; hint: string; icon: JSX.Element }[] = [
  { tier: "brain", label: "Brain", hint: "The model that thinks and writes the answers", icon: <Brain className="h-3.5 w-3.5" /> },
  { tier: "tts", label: "Voice", hint: "Speaks the answers out loud (text-to-speech)", icon: <Volume2 className="h-3.5 w-3.5" /> },
  { tier: "stt", label: "Hearing", hint: "Turns what you say into text (speech-to-text)", icon: <Mic className="h-3.5 w-3.5" /> },
];

// Activate (select) a provider by its tier — mirrors the main Settings API-Keys
// section so onboarding lets the user pick which provider is active per class.
// Realtime has no onboarding step of its own (see TIERS above — it stays a
// Settings-only tier for now), but the map must stay total over ProviderTier.
const SWITCH: Record<ProviderTier, (id: string) => Promise<unknown>> = {
  brain: switchBrainProvider,
  tts: switchTtsProvider,
  stt: switchSttProvider,
  realtime: switchRealtimeProvider,
};

export function ApiKeysStep({ goNext, skip }: StepProps) {
  const t = useT();
  const { providers, loading, error, refetch, setActiveOptimistic } = useProviders();

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
          <KeyRound className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <h2 className="font-display text-lg font-semibold">
            {t("onboarding.api_keys.title")}
          </h2>
          <p className="text-xs text-muted-foreground">{t("onboarding.api_keys.body")}</p>
        </div>
      </div>

      <p className="flex items-center gap-1.5 rounded-md bg-emerald-500/10 px-3 py-2 text-xs text-emerald-600">
        <Check className="h-3.5 w-3.5 shrink-0" />
        {t("onboarding.api_keys.works_now")}
      </p>

      {loading && <p className="text-xs text-muted-foreground">…</p>}
      {error && (
        <p className="text-xs text-amber-500">{t("onboarding.api_keys.body")}</p>
      )}

      {/* One scroll container holding every provider class in order, so the user
          scrolls from Brain through Voice, Hearing and the Subagent section —
          the same content as Settings → API Keys, fitted into the modal. The
          max-height keeps the onboarding card inside the viewport; only this
          list scrolls. Symmetric px-2/py-1 (compensated by -mx-2 so the content
          stays flush with the modal) gives the active card's ring-1 highlight
          room on every side — overflow-y-auto also clips horizontally, so
          without left padding the ring was cut off on the left edge. */}
      <div className="-mx-2 max-h-[52vh] space-y-6 overflow-y-auto scrollbar-jarvis px-2 py-1">
        {TIERS.map((meta) => {
          const list = providers.filter(
            (p) => p.tier === meta.tier && p.brain_switchable !== false,
          );
          if (!list.length) return null;
          return (
            <section key={meta.tier}>
              <div className="mb-2">
                <h3 className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-foreground/80">
                  {meta.icon} {meta.label}
                </h3>
                <p className="text-[11px] leading-tight text-muted-foreground">{meta.hint}</p>
              </div>
              <div className="flex flex-col gap-3">
                {list.map((p) => (
                  <ProviderRow
                    key={p.id}
                    provider={p}
                    onChanged={refetch}
                    onActivateOptimistic={setActiveOptimistic}
                  />
                ))}
              </div>
            </section>
          );
        })}

        {/* Jarvis-Agent (Heavy-Task worker) — own data source (/api/jarvis-agent/status),
            rendered as a sibling section so the onboarding key step matches the
            full Settings → API Keys layout. */}
        <JarvisAgentSection />
      </div>

      <Button className="w-full" onClick={goNext}>
        {t("onboarding.nav.next")}
      </Button>
      <button className="text-xs text-muted-foreground underline" onClick={skip}>
        {t("onboarding.api_keys.skip")}
      </button>
    </div>
  );
}

function ProviderRow({
  provider,
  onChanged,
  onActivateOptimistic,
}: {
  provider: ProviderDescriptor;
  onChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
}) {
  const [busy, setBusy] = useState(false);

  // Selectable when it can actually serve: already active, has its key,
  // needs no key (local), or Codex's brain is ready.
  const usable =
    provider.active ||
    provider.configured ||
    provider.auth_mode === "none" ||
    Boolean(provider.codex_brain_ready);

  async function activate() {
    if (provider.active || !usable || busy) return;
    // Flip the highlight immediately so the switch feels instant — the backend
    // call below rebuilds the provider and can take a few seconds. The refetch
    // afterwards confirms server truth; on failure it rolls the highlight back.
    onActivateOptimistic(provider.tier, provider.id);
    setBusy(true);
    try {
      await SWITCH[provider.tier](provider.id);
      onChanged();
    } catch {
      // Activation failed (e.g. backend rejected) — refetch to roll the
      // optimistic highlight back to the true active provider.
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className={`card-outline space-y-2 p-3 ${provider.active ? "ring-1 ring-primary" : ""}`}
    >
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={activate}
          disabled={provider.active || !usable || busy}
          aria-pressed={provider.active}
          title={provider.active ? "Active" : usable ? "Use this provider" : "Add a key first"}
          className="flex items-center gap-2 text-left disabled:cursor-default"
        >
          <span
            className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${
              provider.active ? "border-primary bg-primary" : "border-muted-foreground/50"
            }`}
          >
            {provider.active && <Check className="h-2.5 w-2.5 text-background" />}
          </span>
          <span className="text-sm font-medium">{provider.label}</span>
        </button>
        <StatusChip configured={provider.configured} active={provider.active} />
      </div>
      <ProviderBillingBadge billing={provider.billing} />
      {provider.auth_mode === "api_key" &&
        provider.secret_keys.map((k) => (
          <ApiKeyForm
            key={k}
            secretKey={k}
            dashboardUrl={provider.dashboard_url}
            configured={Boolean(provider.secrets_set[k])}
            credentialHelp={provider.credential_help}
            onChanged={onChanged}
          />
        ))}
      {provider.auth_mode === "api_key" && provider.alt_credential && (
        <AltCredentialNote alt={provider.alt_credential} />
      )}
      {provider.auth_mode === "codex" && (
        <p className="text-xs text-muted-foreground">
          Sign in with the official Codex / ChatGPT login from Settings → API Keys.
        </p>
      )}
      {provider.auth_mode === "none" && (
        <p className="text-xs text-muted-foreground">Local provider — no key needed.</p>
      )}
    </div>
  );
}

function StatusChip({ configured, active }: { configured: boolean; active: boolean }) {
  if (active) {
    return (
      <span className="rounded-full bg-primary/15 px-2 py-0.5 text-[10px] uppercase tracking-wider text-primary">
        active
      </span>
    );
  }
  return configured ? (
    <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-600">
      ready
    </span>
  ) : (
    <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
      open
    </span>
  );
}
