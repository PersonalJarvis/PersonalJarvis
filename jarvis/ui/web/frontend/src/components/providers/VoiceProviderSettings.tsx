import { useState } from "react";
import { Check, Radio } from "lucide-react";
import { LiveProfile } from "./LiveProfile";
import { ProviderCard } from "./ProviderTierSection";
import { ProviderLogo } from "./ProviderLogo";
import { Button } from "@/components/ui/button";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import {
  type ProviderDescriptor,
  type ProviderTier,
  type SectionHealth,
  sectionHealthForSubject,
} from "@/hooks/useProviders";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

export function VoiceProviderSettings({
  providers,
  loading,
  error,
  onChanged,
  onActivateOptimistic,
  health,
  localMode,
  onDisableLocalMode,
}: {
  providers: ProviderDescriptor[];
  loading: boolean;
  error: string | null;
  onChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
  health?: SectionHealth;
  localMode: boolean;
  onDisableLocalMode: () => void;
}) {
  const t = useT();
  const runtime = useVoiceMode();
  const [inspectedId, setInspectedId] = useState<string | null>(null);
  const choices = providers.filter(
    (provider) =>
      provider.tier === "realtime" &&
      (!localMode || provider.billing === "local"),
  );
  const selected =
    choices.find((provider) => provider.id === inspectedId) ??
    choices.find((provider) => provider.active) ??
    choices[0];
  return (
    <section
      role="tabpanel"
      aria-label={t("live.setup_title")}
      className="space-y-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold tracking-tight">
            {t("live.setup_title")}
          </h2>
          <p className="mt-1 max-w-xl text-sm leading-relaxed text-muted-foreground">
            {t("live.setup_description")}
          </p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground">
          <Radio className="h-3 w-3" />
          {runtime.sessionActive ? t("live.call_active") : t("live.next_call")}
        </span>
      </div>
      {runtime.sessionActive && runtime.activeSessionMode === "pipeline" && (
        <p
          role="status"
          data-testid="voice-engine-runtime-status"
          className="rounded-lg border border-warning/30 px-3 py-2 text-sm text-warning"
        >
          {t("live.pipeline_fallback")}
        </p>
      )}
      {runtime.lastStartError && (
        <p
          role="alert"
          className="rounded-lg border border-destructive/30 px-3 py-2 text-sm text-destructive"
        >
          {runtime.lastStartError.message}
        </p>
      )}
      {loading && !choices.length && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("live.loading")}
        </p>
      )}
      {error && (
        <div
          role="alert"
          className="flex items-center gap-3 text-sm text-destructive"
        >
          <span>{error}</span>
          <Button variant="outline" onClick={onChanged}>
            {t("common.retry")}
          </Button>
        </div>
      )}
      <div
        role="group"
        aria-label={t("live.provider_selection")}
        className="grid grid-cols-2 gap-2 lg:grid-cols-4"
      >
        {choices.map((provider) => (
          <button
            key={provider.id}
            type="button"
            aria-pressed={selected?.id === provider.id}
            onClick={() => setInspectedId(provider.id)}
            className={cn(
              "flex min-w-0 items-center gap-2.5 rounded-xl border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              selected?.id === provider.id
                ? "border-foreground/30 bg-secondary"
                : "border-border bg-card hover:bg-secondary/60",
            )}
          >
            <ProviderLogo providerId={provider.id} label={provider.label} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">
                {provider.label}
              </span>
              <span className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                {provider.active && <Check className="h-3 w-3" />}
                {provider.active
                    ? t("live.active")
                  : provider.billing === "local"
                    ? t("live.local_provider")
                    : provider.configured
                      ? t("live.connected")
                      : t("live.connect")}
              </span>
            </span>
          </button>
        ))}
      </div>
      {selected && (
        <div className="overflow-hidden rounded-xl border border-border bg-card">
          <ProviderCard
            key={selected.id}
            descriptor={selected}
            onChanged={onChanged}
            onActivateOptimistic={onActivateOptimistic}
            autoActivateOnSave={false}
            health={
              selected.active
                ? sectionHealthForSubject(health, selected.id)
                : undefined
            }
            configuration={
              selected.configuration_surface === "live" ? (
                <LiveProfile onSaved={onChanged} />
              ) : undefined
            }
          />
        </div>
      )}
      {localMode && !choices.length && (
        <Button variant="outline" onClick={onDisableLocalMode}>
          {t("live.show_cloud")}
        </Button>
      )}
      <p className="text-xs leading-relaxed text-muted-foreground">
        {t("live.agents_separate")}
      </p>
    </section>
  );
}
