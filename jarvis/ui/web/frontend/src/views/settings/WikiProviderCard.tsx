import { useState } from "react";
import { BookOpen, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { saveWikiProvider, useWikiProvider } from "@/hooks/useWikiProvider";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

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
    <section>
      <h3 className="mb-3 inline-flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
        <BookOpen className="h-3.5 w-3.5" /> {t("wiki_provider.tier_label")}
      </h3>

      <div className="card-outline space-y-3 p-4">
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("wiki_provider.description")}
        </p>

        {loading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> {t("wiki_provider.loading")}
          </div>
        )}

        {error && <p className="text-xs text-destructive">{t("wiki_provider.load_error")}</p>}

        {!loading && data && (
          <div className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
                {t("wiki_provider.provider_label")}
              </span>
              <select
                aria-label={t("wiki_provider.provider_label")}
                value={providerValue}
                onChange={(e) => handleProviderChange(e.target.value)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
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
            </label>

            <label className="block">
              <span className="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
                {t("wiki_provider.model_label")}
              </span>
              <select
                aria-label={t("wiki_provider.model_label")}
                value={modelValue}
                onChange={(e) => setModel(e.target.value)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm"
              >
                <option value={FOLLOW_PRIMARY}>{t("wiki_provider.model_follow_primary")}</option>
                {modelOptions.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              <span className="mt-1 block text-[11px] text-muted-foreground">
                {t("wiki_provider.model_hint")}
              </span>
            </label>

            <Button onClick={handleApply} disabled={pending} className="w-full">
              {pending ? t("wiki_provider.applying") : t("wiki_provider.apply")}
            </Button>
          </div>
        )}
      </div>
    </section>
  );
}
