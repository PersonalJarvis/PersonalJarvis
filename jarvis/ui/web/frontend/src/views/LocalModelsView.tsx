/**
 * Local models — the section for the models that run on this machine (or on
 * a server the user names): the Ollama runtime, the installed models, the
 * public catalogue and Hugging Face.
 *
 * The column runs the full window width (a catalogue is browsed, not read)
 * and the header carries a rail of tabs: Overview (what sits in memory, the
 * jobs, the server button), Models (the installed ledger with the Tune
 * sheet), Browse models, Hugging Face and Server. Every tab is always
 * there — the Simple/Advanced switch that used to hide two of them is gone,
 * because a control that is hidden is a control that is searched for.
 * "Tune" from a job card opens the Tune sheet for that model right under
 * the overview, so a non-developer never has to find the model in the
 * ledger.
 *
 * Everything is gated on the capability `supports_model_pull`, never on a
 * provider name — a second pull-capable server later gets the same section.
 * The id of that card is seeded from `localStorage` (`localModelsSeed.ts`), so
 * from the second open on the panels mount synchronously instead of waiting
 * for `/api/providers`; the list still resolves and corrects the seed.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  BackLink,
  Panel,
  PanelHeader,
  SegmentedFilter,
  SoftButton,
} from "@/components/extensions/primitives";
import { useInventory, useReloadLocalModels } from "@/hooks/useLocalModels";
import { useProviders } from "@/hooks/useProviders";
import {
  LOCAL_MODELS_MARK_MOUNT,
  markLocalModels,
} from "@/lib/localModelsPerf";
import {
  clearLocalModelsSeed,
  readLocalModelsSeed,
  writeLocalModelsSeed,
} from "@/lib/localModelsSeed";
import { CataloguePanel } from "@/views/local-models/CataloguePanel";
import { HuggingFacePanel } from "@/views/local-models/HuggingFacePanel";
import { InventoryPanel } from "@/views/local-models/InventoryPanel";
import { OverviewPanel } from "@/views/local-models/OverviewPanel";
import { ServerPanel } from "@/views/local-models/ServerPanel";
import { TuneSheet } from "@/views/local-models/TuneSheet";
import { useEventStore } from "@/store/events";
import { useLocaleChunk, useT } from "@/i18n";

export type LocalModelsTab =
  "overview" | "models" | "catalogue" | "huggingface" | "server";

/**
 * The Tune sheet opened from a role row. The sheet wants the inventory row
 * (capabilities, native context, size), so it is looked up here; a model the
 * inventory no longer lists gets one honest sentence instead of a blank sheet.
 */
function RoleTuneDrawer({
  providerId,
  model,
  onClose,
}: {
  providerId: string;
  model: string;
  onClose: () => void;
}) {
  const t = useT();
  const inventory = useInventory(providerId);
  const row = inventory.data?.models.find((m) => m.name === model) ?? null;
  return (
    <div data-testid="local-models-tune-drawer">
      <Panel className="p-4">
        {row ? (
          <TuneSheet providerId={providerId} model={row} onClose={onClose} />
        ) : (
          <p className="text-sm text-muted-foreground">
            {inventory.isLoading
              ? t("local_models.loading")
              : t("local_models.tune_missing")}
          </p>
        )}
      </Panel>
    </div>
  );
}

export function LocalModelsView() {
  const t = useT();
  useLocaleChunk("local_models");
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const { providers, loading } = useProviders();
  useEffect(() => {
    markLocalModels(LOCAL_MODELS_MARK_MOUNT);
  }, []);

  // The one server that can download models. Capability, not provider id.
  const descriptor = useMemo(
    () => providers.find((p) => Boolean(p.supports_model_pull)) ?? null,
    [providers],
  );
  // Last known pull-capable id: paints the panels before the list resolves.
  const [seed, setSeed] = useState<string | null>(readLocalModelsSeed);
  useEffect(() => {
    if (loading) return;
    if (descriptor) {
      writeLocalModelsSeed(descriptor.id);
      setSeed(descriptor.id);
    } else {
      clearLocalModelsSeed();
      setSeed(null);
    }
  }, [loading, descriptor]);
  const providerId: string | null = descriptor?.id ?? (loading ? seed : null);

  const [tab, setTab] = useState<LocalModelsTab>("overview");
  // The model whose Tune sheet is open under the overview ("" = none).
  const [tuneModel, setTuneModel] = useState<string>("");
  const openBrowse = useCallback(() => setTab("catalogue"), []);
  const openManage = useCallback(() => setTab("models"), []);
  const openApiKeys = useCallback(
    () => setActiveSection("apikeys"),
    [setActiveSection],
  );
  const closeTune = useCallback(() => setTuneModel(""), []);

  // Refresh: drops the painted snapshot and every cached answer, then reads
  // the server again. The one recovery from a snapshot taken while the
  // server was silent, which otherwise paints a wiped machine (BUG-188).
  const reload = useReloadLocalModels(providerId ?? undefined);
  const [reloading, setReloading] = useState(false);
  const onReload = useCallback(() => {
    if (reloading) return;
    setReloading(true);
    void reload().finally(() => setReloading(false));
  }, [reload, reloading]);

  const tabs = useMemo(
    () =>
      [
        { id: "overview", label: t("local_models.tab_overview") },
        { id: "models", label: t("local_models.tab_models") },
        { id: "catalogue", label: t("local_models.tab_catalogue") },
        { id: "huggingface", label: t("local_models.tab_huggingface") },
        { id: "server", label: t("local_models.tab_server") },
      ] as { id: LocalModelsTab; label: string }[],
    [t],
  );

  return (
    <div className="flex h-full min-h-0 w-full flex-col">
      <div className="flex w-full shrink-0 flex-col gap-4 px-8 py-6">
        <BackLink
          label={t("local_models.back")}
          onClick={() => setActiveSection("apikeys")}
        />
        <PanelHeader
          title={t("local_models.title")}
          subtitle={t("local_models.subtitle")}
        />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <SegmentedFilter<LocalModelsTab>
            label={t("local_models.tabs_label")}
            value={tab}
            onChange={setTab}
            options={tabs}
          />
          <SoftButton
            onClick={onReload}
            disabled={!providerId || reloading}
            ariaLabel={t("local_models.reload")}
            className="h-8"
          >
            {reloading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            {t("local_models.reload")}
          </SoftButton>
        </div>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="flex w-full flex-col gap-4 px-8 pb-6">
          {loading && !providerId && (
            <p className="text-sm text-muted-foreground">
              {t("local_models.loading")}
            </p>
          )}

          {!loading && !providerId && (
            <Panel className="p-4">
              <p className="text-sm text-muted-foreground">
                {t("local_models.no_provider")}
              </p>
            </Panel>
          )}

          {providerId && tab === "overview" && (
            <div className="space-y-4" data-testid="local-models-overview">
              <OverviewPanel
                providerId={providerId}
                onTune={setTuneModel}
                onOpenApiKeys={openApiKeys}
                onBrowse={openBrowse}
                onManage={openManage}
              />
              {tuneModel && (
                <RoleTuneDrawer
                  providerId={providerId}
                  model={tuneModel}
                  onClose={closeTune}
                />
              )}
            </div>
          )}

          {providerId && tab === "models" && (
            <div data-testid="local-models-models">
              <InventoryPanel providerId={providerId} />
            </div>
          )}

          {providerId && tab === "catalogue" && (
            <Panel className="p-4">
              <div className="space-y-3" data-testid="local-models-catalogue">
                <PanelHeader
                  title={t("local_models.catalogue_title")}
                  subtitle={t("local_models.catalogue_subtitle")}
                />
                <CataloguePanel providerId={providerId} />
              </div>
            </Panel>
          )}

          {providerId && tab === "huggingface" && (
            <Panel className="p-4">
              <div className="space-y-3" data-testid="local-models-huggingface">
                <PanelHeader title={t("local_models.huggingface_title")} />
                <HuggingFacePanel providerId={providerId} />
              </div>
            </Panel>
          )}

          {providerId && tab === "server" && (
            <div data-testid="local-models-server">
              <ServerPanel providerId={providerId} />
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
