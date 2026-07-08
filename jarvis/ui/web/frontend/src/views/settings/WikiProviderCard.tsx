import { useState } from "react";
import { BookOpen, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { saveWikiProvider, useWikiProvider } from "@/hooks/useWikiProvider";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import { SettingsBlock, SettingsField, settingsInputCls } from "@/views/settings/SettingsBlock";

// Empty string = "follow brain.primary" (provider) / "cheap default" (model).
// We render it as a named option so the user can deliberately pick "Same as
// brain" instead of guessing what blank does.
const FOLLOW_PRIMARY = "";

/**
 * "Wiki" tier card in the API-Keys & Providers screen. Lets the user pick the
 * dedicated Wiki-curator provider + (optional) model via
 * `GET/PUT /api/settings/wiki-provider`. The endpoint exposes `available` as a
 * list of `{ provider, models[] }` objects, so the provider `<select>` is fed by
 * the provider ids and the model `<select>` is fed by the chosen provider's
 * `models[]` plus a leading "Same as brain (cheap default)" option. An empty
 * provider/model is intentional and means "follow brain.primary" / "use the
 * cheap-fast model of the chosen provider" (the ack-brain follow_brain pattern),
 * so both fields are optional and surfaced as the "Same as brain" option.
 */
export function WikiProviderCard() {
  const t = useT();
  const { data, loading, error, refetch } = useWikiProvider();
  const pushToast = useEventStore((s) => s.pushToast);

  const [provider, setProvider] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Controlled values fall back to the server state until the user edits them.
  const providerValue = provider ?? data?.provider ?? FOLLOW_PRIMARY;
  const modelValue = model ?? data?.model ?? FOLLOW_PRIMARY;

  // Model options come from the chosen provider's `models[]`. When the provider
  // is "Same as brain" (empty), there is no concrete model list to pin, so only
  // the cheap-default option is offered.
  const modelOptions =
    data?.available.find((p) => p.provider === providerValue)?.models ?? [];

  async function handleApply() {
    setPending(true);
    try {
      const next = await saveWikiProvider(providerValue, modelValue);
      // Reset local edits so the inputs re-sync to the server-resolved state.
      setProvider(null);
      setModel(null);
      pushToast(
        "success",
        next.provider
          ? `Wiki → ${next.provider}${next.model ? ` · ${next.model}` : ""}`
          : t("wiki_provider.follow_primary"),
      );
      void refetch();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  // Picking a new provider invalidates the previously selected model (it may not
  // exist under the new provider), so we reset the model back to the cheap
  // default whenever the provider changes.
  function handleProviderChange(next: string) {
    setProvider(next);
    setModel(FOLLOW_PRIMARY);
  }

  return (
    <SettingsBlock
      icon={BookOpen}
      title={t("wiki_provider.tier_label")}
      description={t("wiki_provider.description")}
    >
      {loading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("wiki_provider.loading")}
        </div>
      )}

      {error && <p className="text-xs text-destructive">{t("wiki_provider.load_error")}</p>}

      {!loading && data && (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <SettingsField label={t("wiki_provider.provider_label")}>
              <select
                aria-label={t("wiki_provider.provider_label")}
                value={providerValue}
                onChange={(e) => handleProviderChange(e.target.value)}
                className={settingsInputCls}
              >
                <option value={FOLLOW_PRIMARY}>{t("wiki_provider.follow_primary")}</option>
                {data.available
                  .filter((p) => p.provider !== FOLLOW_PRIMARY)
                  .map((p) => (
                    <option key={p.provider} value={p.provider}>
                      {p.provider}
                    </option>
                  ))}
              </select>
            </SettingsField>

            <SettingsField label={t("wiki_provider.model_label")}>
              <select
                aria-label={t("wiki_provider.model_label")}
                value={modelValue}
                onChange={(e) => setModel(e.target.value)}
                className={cn(settingsInputCls, "font-mono")}
              >
                <option value={FOLLOW_PRIMARY}>{t("wiki_provider.model_follow_primary")}</option>
                {modelOptions.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </SettingsField>
          </div>

          <p className="text-[11px] text-muted-foreground">
            {t("wiki_provider.model_hint")}
          </p>

          <Button onClick={handleApply} disabled={pending} size="sm">
            {pending ? t("wiki_provider.applying") : t("wiki_provider.apply")}
          </Button>
        </div>
      )}
    </SettingsBlock>
  );
}
