import { useState } from "react";
import { Brain, Check, Mic, Volume2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { ApiKeyForm } from "@/components/ApiKeyForm";
import {
  useProviders,
  switchBrainProvider,
  switchTtsProvider,
  switchSttProvider,
  type ProviderDescriptor,
  type ProviderTier,
} from "@/hooks/useProviders";
import type { StepProps } from "../OnboardingFlow";

// One provider class per internal page — Brain first, then Voice, then Hearing.
// Kept as a SINGLE flow step (no backend ONBOARDING_STEPS / parity change); the
// paging lives inside this component so users see one class at a time instead of
// a wall of every provider at once.
const TIERS: { tier: ProviderTier; label: string; icon: JSX.Element }[] = [
  { tier: "brain", label: "Brain — reasoning", icon: <Brain className="h-5 w-5" /> },
  { tier: "tts", label: "Voice — text to speech", icon: <Volume2 className="h-5 w-5" /> },
  { tier: "stt", label: "Hearing — speech to text", icon: <Mic className="h-5 w-5" /> },
];

// Activate (select) a provider by its tier — mirrors the main Settings API-Keys
// section so onboarding lets the user pick which provider is active per class.
const SWITCH: Record<ProviderTier, (id: string) => Promise<unknown>> = {
  brain: switchBrainProvider,
  tts: switchTtsProvider,
  stt: switchSttProvider,
};

export function ApiKeysStep({ goNext, skip }: StepProps) {
  const t = useT();
  const { providers, loading, error, refetch } = useProviders();
  const [ti, setTi] = useState(0);
  const cur = TIERS[ti];
  const list = providers.filter((p) => p.tier === cur.tier);
  const isLastTier = ti >= TIERS.length - 1;

  const next = () => (isLastTier ? goNext() : setTi((i) => i + 1));

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
          {cur.icon}
        </div>
        <div className="min-w-0">
          <h2 className="font-display text-lg font-semibold">{cur.label}</h2>
          <p className="text-xs text-muted-foreground">
            {t("onboarding.api_keys.body")} · {ti + 1}/{TIERS.length}
          </p>
        </div>
      </div>

      {cur.tier === "brain" && (
        <p className="flex items-center gap-1.5 rounded-md bg-emerald-500/10 px-3 py-2 text-xs text-emerald-600">
          <Check className="h-3.5 w-3.5 shrink-0" />
          {t("onboarding.api_keys.works_now")}
        </p>
      )}

      {loading && <p className="text-xs text-muted-foreground">…</p>}
      {error && (
        <p className="text-xs text-amber-500">{t("onboarding.api_keys.body")}</p>
      )}

      <div className="flex flex-col gap-3">
        {list.map((p) => (
          <ProviderRow key={p.id} provider={p} onChanged={refetch} />
        ))}
      </div>

      <Button className="w-full" onClick={next}>
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
}: {
  provider: ProviderDescriptor;
  onChanged: () => void;
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
    setBusy(true);
    try {
      await SWITCH[provider.tier](provider.id);
      onChanged();
    } catch {
      // Activation failed (e.g. backend rejected) — leave the list as-is;
      // the status chip keeps reflecting the unchanged active provider.
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
      {provider.auth_mode === "api_key" &&
        provider.secret_keys.map((k) => (
          <ApiKeyForm
            key={k}
            secretKey={k}
            dashboardUrl={provider.dashboard_url}
            configured={Boolean(provider.secrets_set[k])}
            onChanged={onChanged}
          />
        ))}
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
