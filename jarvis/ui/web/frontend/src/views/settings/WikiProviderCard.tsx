import { useState } from "react";
import { BookOpen, ChevronDown, Info, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { BrainModelSelector } from "@/components/BrainModelSelector";
import type { BrainModelSaveResult } from "@/hooks/useProviders";
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
 * `GET/PUT /api/settings/wiki-provider`.
 *
 * The provider `<select>` is fed by the endpoint's `available` matrix (agent
 * providers such as Codex are labelled, keyless ones flagged). The model pick
 * uses the shared searchable {@link BrainModelSelector} against the chosen
 * provider's live catalog (`GET /api/providers/{id}/models`) — the same picker
 * the Jarvis-Agent model pin uses, so e.g. Codex offers its real model lineup
 * instead of an empty tier-default list. Picking a model saves provider+model
 * in one PUT; the Apply button remains for provider-only changes.
 *
 * An empty provider/model is intentional and means "follow brain.primary" /
 * "use the cheap-fast model of the chosen provider". The `resolved` line below
 * the pickers states what the NEXT maintenance run will actually use (resolved
 * server-side by the same helper the runtime uses).
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

  // Shared save path: provider+model always travel together in one PUT, so the
  // Apply button, the model picker, and the reset link can never disagree.
  async function applySelection(nextProvider: string, nextModel: string) {
    const next = await saveWikiProvider(nextProvider, nextModel);
    // Reset local edits so the inputs re-sync to the server-resolved state.
    setProvider(null);
    setModel(null);
    void refetch();
    return next;
  }

  async function handleApply() {
    setPending(true);
    try {
      const next = await applySelection(providerValue, modelValue);
      pushToast(
        "success",
        next.provider
          ? `Wiki → ${next.provider}${next.model ? ` · ${next.model}` : ""}`
          : t("wiki_provider.follow_primary"),
      );
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  // The model picker saves immediately (same UX as the Jarvis-Agent model pin):
  // returning the save result lets BrainModelSelector render its own toast.
  async function handleModelSave(m: string): Promise<BrainModelSaveResult> {
    const next = await applySelection(providerValue, m);
    return {
      ok: true,
      provider: next.provider,
      model: next.model,
      persisted: next.persisted ?? true,
      applied_live: next.applied_live ?? true,
      restart_required: next.restart_required ?? false,
      probe: null,
    };
  }

  async function handleModelReset() {
    setPending(true);
    try {
      await applySelection(providerValue, FOLLOW_PRIMARY);
      pushToast("success", t("wiki_provider.model_follow_primary"));
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

  function providerOptionLabel(p: { provider: string; kind?: string; ready?: boolean }) {
    let label = p.provider;
    if (p.kind === "agent") label += ` — ${t("wiki_provider.option_agent_suffix")}`;
    if (p.ready === false) label += ` (${t("wiki_provider.option_no_key")})`;
    return label;
  }

  const resolved = data?.resolved;

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
          {/* "How does this work?" — native <details>, the app's collapsible idiom. */}
          <details className="group rounded-md border border-border/60 bg-muted/20 px-3 py-2">
            <summary className="flex cursor-pointer select-none list-none items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
              <Info className="h-3.5 w-3.5 shrink-0" />
              {t("wiki_provider.how_title")}
              <ChevronDown className="h-3 w-3 shrink-0 transition-transform group-open:rotate-180" />
            </summary>
            <div className="mt-2 space-y-1.5 text-xs leading-relaxed text-muted-foreground">
              <p>{t("wiki_provider.how_intro")}</p>
              <ol className="list-decimal space-y-1 pl-4">
                <li>{t("wiki_provider.how_step1")}</li>
                <li>{t("wiki_provider.how_step2")}</li>
                <li>{t("wiki_provider.how_step3")}</li>
              </ol>
              <p>{t("wiki_provider.how_model_note")}</p>
            </div>
          </details>

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
                      {providerOptionLabel(p)}
                    </option>
                  ))}
              </select>
            </SettingsField>

            {providerValue === FOLLOW_PRIMARY ? (
              // No concrete provider → there is no model list to pick from, so
              // no dead dropdown: a static line states what "empty" means.
              <SettingsField label={t("wiki_provider.model_label")}>
                <div className={cn(settingsInputCls, "flex items-center text-muted-foreground")}>
                  {t("wiki_provider.model_follow_primary")}
                </div>
              </SettingsField>
            ) : (
              <div className="space-y-1.5">
                <BrainModelSelector
                  providerId={providerValue}
                  controlled
                  currentModel={modelValue}
                  headingLabel={t("wiki_provider.model_label")}
                  placeholder={t("wiki_provider.model_follow_primary")}
                  onSave={handleModelSave}
                />
                {modelValue !== FOLLOW_PRIMARY && (
                  <button
                    type="button"
                    onClick={() => void handleModelReset()}
                    disabled={pending}
                    className="text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                  >
                    {t("wiki_provider.model_reset")}
                  </button>
                )}
              </div>
            )}
          </div>

          {/* Honest ground truth: what the next maintenance run will actually use. */}
          {resolved && resolved.provider && (
            <div className="space-y-1">
              <p className="text-[11px] text-muted-foreground">
                {t("wiki_provider.resolved_label")}{" "}
                <span className="font-mono text-foreground/80">
                  {resolved.provider}
                  {" · "}
                  {resolved.model || t("wiki_provider.provider_default_model")}
                </span>
              </p>
              {resolved.ready === false && (
                <p className="text-[11px] text-amber-500">
                  {t("wiki_provider.resolved_fallback_warning")}
                </p>
              )}
            </div>
          )}

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
