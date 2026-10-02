import { Button } from "@/components/ui/button";
import type { ProviderDescriptor, ProviderTier, SectionHealth } from "@/hooks/useProviders";
import { useT } from "@/i18n";
import { RealtimeTab } from "@/views/apikeys/RealtimeTab";

/** Keep the public voice-mode shell while sharing the realtime provider setup. */
export function VoiceProviderSettings({
  providers, loading, error, onChanged, onActivateOptimistic, health,
  localMode, onDisableLocalMode,
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
  const visible = providers.filter(
    (provider) => provider.tier === "realtime" && (!localMode || provider.billing === "local"),
  );
  return (
    <section role="tabpanel" aria-label={t("live.setup_title")} className="space-y-5">
      <RealtimeTab providers={visible} loading={loading} error={error}
        onChanged={onChanged} onActivateOptimistic={onActivateOptimistic} health={health} />
      {localMode && !visible.length ? (
        <Button variant="outline" onClick={onDisableLocalMode}>
          {t("live.show_cloud")}
        </Button>
      ) : null}
      <p className="text-xs leading-relaxed text-muted-foreground">
        {t("live.agents_separate")}
      </p>
    </section>
  );
}
