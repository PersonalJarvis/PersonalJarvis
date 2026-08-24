/**
 * Local models — the section for the models that run on this machine (or on
 * a server the user names): the Ollama runtime, the installed models, the
 * public catalogue and, later, Hugging Face.
 *
 * Step 1 of the plan: the skeleton. The column layout is the Costs view's,
 * the header carries a Simple | Advanced switch (persisted per browser) and a
 * rail of tabs. Models and Hugging Face are Advanced-only; the Catalogue and
 * Server tabs already mount the existing provider-card panels, the others
 * hold a placeholder until their own step lands.
 *
 * Everything is gated on the capability `supports_model_pull`, never on a
 * provider name — a second pull-capable server later gets the same section.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  BackLink,
  Panel,
  PanelHeader,
  SegmentedFilter,
} from "@/components/extensions/primitives";
import {
  BaseUrlField,
  LocalModelDownloadPanel,
  OllamaRuntimePanel,
} from "@/components/providers/ProviderTierSection";
import { useProviders } from "@/hooks/useProviders";
import { useEventStore } from "@/store/events";
import { useLocaleChunk, useT } from "@/i18n";

export type LocalModelsMode = "simple" | "advanced";
export type LocalModelsTab = "overview" | "models" | "catalogue" | "huggingface" | "server";

/** Browser-local preference; a private window simply starts on Simple. */
export const LOCAL_MODELS_MODE_KEY = "jarvis.localModels.mode";

const ADVANCED_ONLY: ReadonlySet<LocalModelsTab> = new Set(["models", "huggingface"]);

function readStoredMode(): LocalModelsMode {
  try {
    return window.localStorage.getItem(LOCAL_MODELS_MODE_KEY) === "advanced"
      ? "advanced"
      : "simple";
  } catch {
    // Storage can be blocked (privacy mode, embedded WebView policies); the
    // preference is a convenience, so the default is the honest answer.
    return "simple";
  }
}

function storeMode(mode: LocalModelsMode): void {
  try {
    window.localStorage.setItem(LOCAL_MODELS_MODE_KEY, mode);
  } catch {
    // Same as above: nothing to recover, the in-memory state still applies.
  }
}

export function LocalModelsView() {
  const t = useT();
  useLocaleChunk("local_models");
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const { providers, loading, refetch } = useProviders();

  // The one server that can download models. Capability, not provider id.
  const descriptor = useMemo(
    () => providers.find((p) => Boolean(p.supports_model_pull)) ?? null,
    [providers],
  );

  const [mode, setMode] = useState<LocalModelsMode>(readStoredMode);
  const [tab, setTab] = useState<LocalModelsTab>("overview");

  const changeMode = useCallback((next: LocalModelsMode) => {
    setMode(next);
    storeMode(next);
  }, []);

  // Leaving Advanced while on an Advanced-only tab must not strand the user on
  // a tab the rail no longer shows.
  useEffect(() => {
    if (mode === "simple" && ADVANCED_ONLY.has(tab)) setTab("overview");
  }, [mode, tab]);

  const onChanged = useCallback(() => {
    void refetch();
  }, [refetch]);

  const tabs = useMemo(() => {
    const all: { id: LocalModelsTab; label: string }[] = [
      { id: "overview", label: t("local_models.tab_overview") },
      { id: "models", label: t("local_models.tab_models") },
      { id: "catalogue", label: t("local_models.tab_catalogue") },
      { id: "huggingface", label: t("local_models.tab_huggingface") },
      { id: "server", label: t("local_models.tab_server") },
    ];
    return mode === "advanced" ? all : all.filter((o) => !ADVANCED_ONLY.has(o.id));
  }, [mode, t]);

  return (
    <ScrollArea className="h-full">
      <div className="mx-auto flex max-w-[1180px] flex-col gap-4 px-6 py-6">
        <BackLink label={t("local_models.back")} onClick={() => setActiveSection("apikeys")} />
        <PanelHeader
          title={t("local_models.title")}
          subtitle={t("local_models.subtitle")}
          actions={
            <SegmentedFilter<LocalModelsMode>
              label={t("local_models.mode_label")}
              value={mode}
              onChange={changeMode}
              options={[
                { id: "simple", label: t("local_models.mode_simple") },
                { id: "advanced", label: t("local_models.mode_advanced") },
              ]}
            />
          }
        />

        <SegmentedFilter<LocalModelsTab>
          label={t("local_models.tabs_label")}
          value={tab}
          onChange={setTab}
          options={tabs}
        />

        {loading && !descriptor && (
          <p className="text-sm text-muted-foreground">{t("local_models.loading")}</p>
        )}

        {!loading && !descriptor && (
          <Panel className="p-4">
            <p className="text-sm text-muted-foreground">{t("local_models.no_provider")}</p>
          </Panel>
        )}

        {descriptor && tab === "overview" && (
          <Panel className="p-4">
            <div className="space-y-3" data-testid="local-models-overview">
              <PanelHeader title={t("local_models.overview_title")} />
              <OllamaRuntimePanel providerId={descriptor.id} onChanged={onChanged} alwaysVisible />
              <p className="text-sm text-muted-foreground">
                {t("local_models.overview_placeholder")}
              </p>
            </div>
          </Panel>
        )}

        {descriptor && tab === "models" && (
          <Panel className="p-4">
            <div className="space-y-3" data-testid="local-models-models">
              <PanelHeader title={t("local_models.models_title")} />
              <p className="text-sm text-muted-foreground">
                {t("local_models.models_placeholder")}
              </p>
            </div>
          </Panel>
        )}

        {descriptor && tab === "catalogue" && (
          <Panel className="p-4">
            <div className="space-y-3" data-testid="local-models-catalogue">
              <PanelHeader
                title={t("local_models.catalogue_title")}
                subtitle={t("local_models.catalogue_subtitle")}
              />
              {/* The download panel already embeds the library browser. */}
              <LocalModelDownloadPanel descriptor={descriptor} onChanged={onChanged} />
            </div>
          </Panel>
        )}

        {descriptor && tab === "huggingface" && (
          <Panel className="p-4">
            <div className="space-y-3" data-testid="local-models-huggingface">
              <PanelHeader title={t("local_models.huggingface_title")} />
              <p className="text-sm text-muted-foreground">
                {t("local_models.huggingface_placeholder")}
              </p>
            </div>
          </Panel>
        )}

        {descriptor && tab === "server" && (
          <Panel className="p-4">
            <div className="space-y-3" data-testid="local-models-server">
              <PanelHeader
                title={t("local_models.server_title")}
                subtitle={t("local_models.server_subtitle")}
              />
              <OllamaRuntimePanel providerId={descriptor.id} onChanged={onChanged} alwaysVisible />
              {descriptor.supports_base_url && (
                <BaseUrlField descriptor={descriptor} onChanged={onChanged} />
              )}
            </div>
          </Panel>
        )}
      </div>
    </ScrollArea>
  );
}
