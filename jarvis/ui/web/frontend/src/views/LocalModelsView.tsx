/**
 * Local models — the section for the models that run on this machine (or on
 * a server the user names): the Ollama runtime, the installed models, the
 * public catalogue and, later, Hugging Face.
 *
 * The column runs the full window width (a catalogue is browsed, not read), the header carries a Simple |
 * Advanced switch (persisted per browser) and a rail of tabs. Simple shows
 * Overview (server facts + the four roles), Catalogue and Server; Advanced
 * adds Models (the installed ledger with the Tune sheet) and Hugging Face.
 * "Tune" from a role row opens the Tune sheet for that model right under the
 * overview, so a non-developer never has to find the model in the ledger.
 *
 * Everything is gated on the capability `supports_model_pull`, never on a
 * provider name — a second pull-capable server later gets the same section.
 * The id of that card is seeded from `localStorage` (`localModelsSeed.ts`), so
 * from the second open on the panels mount synchronously instead of waiting
 * for `/api/providers`; the list still resolves and corrects the seed.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  BackLink,
  Panel,
  PanelHeader,
  SegmentedFilter,
} from "@/components/extensions/primitives";
import { useInventory } from "@/hooks/useLocalModels";
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
import {
  AssistantPanel,
  type AssistantRequest,
} from "@/components/local-models/AssistantPanel";
import { CataloguePanel } from "@/views/local-models/CataloguePanel";
import { HuggingFacePanel } from "@/views/local-models/HuggingFacePanel";
import { InventoryPanel } from "@/views/local-models/InventoryPanel";
import { OverviewPanel } from "@/views/local-models/OverviewPanel";
import { ServerPanel } from "@/views/local-models/ServerPanel";
import { TuneSheet } from "@/views/local-models/TuneSheet";
import { useEventStore } from "@/store/events";
import { useLocaleChunk, useT } from "@/i18n";

export type LocalModelsMode = "simple" | "advanced";
export type LocalModelsTab =
  "overview" | "assistant" | "models" | "catalogue" | "huggingface" | "server";

/** Browser-local preference; a private window simply starts on Simple. */
export const LOCAL_MODELS_MODE_KEY = "jarvis.localModels.mode";

const ADVANCED_ONLY: ReadonlySet<LocalModelsTab> = new Set([
  "models",
  "huggingface",
]);

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

  const [mode, setMode] = useState<LocalModelsMode>(readStoredMode);
  const [tab, setTab] = useState<LocalModelsTab>("overview");
  // The model whose Tune sheet is open under the overview ("" = none).
  const [tuneModel, setTuneModel] = useState<string>("");
  // The setup assistant panel under the action row. "Help me set up" toggles
  // it and asks for a setup run; "Something is not working" opens it in
  // diagnose mode (the Server tab with the log stays one link away inside
  // the panel). The token makes a repeated click a repeated request.
  const [assistantRequest, setAssistantRequest] =
    useState<AssistantRequest | null>(null);
  const [serverLogOpen, setServerLogOpen] = useState(false);
  const openBrowse = useCallback(() => setTab("catalogue"), []);
  // The helper is an area of its own, not a block that pushes the overview
  // down: the rail stays on screen, so "Browse models" is always one click
  // away and the page never grows to a scroll of thousands of pixels.
  const openAssistant = useCallback(() => {
    setTab("assistant");
    setAssistantRequest((r) => ({ mode: "setup", token: (r?.token ?? 0) + 1 }));
  }, []);
  const reportProblem = useCallback(() => {
    setTab("assistant");
    setAssistantRequest((r) => ({ mode: "diagnose", token: (r?.token ?? 0) + 1 }));
  }, []);
  const openServerLog = useCallback(() => {
    setServerLogOpen(true);
    setTab("server");
  }, []);
  const openApiKeys = useCallback(
    () => setActiveSection("apikeys"),
    [setActiveSection],
  );
  const closeTune = useCallback(() => setTuneModel(""), []);

  const changeMode = useCallback((next: LocalModelsMode) => {
    setMode(next);
    storeMode(next);
  }, []);

  // Leaving Advanced while on an Advanced-only tab must not strand the user on
  // a tab the rail no longer shows.
  useEffect(() => {
    if (mode === "simple" && ADVANCED_ONLY.has(tab)) setTab("overview");
  }, [mode, tab]);

  const tabs = useMemo(() => {
    const all: { id: LocalModelsTab; label: string }[] = [
      { id: "overview", label: t("local_models.tab_overview") },
      { id: "assistant", label: t("local_models.tab_assistant") },
      { id: "models", label: t("local_models.tab_models") },
      { id: "catalogue", label: t("local_models.tab_catalogue") },
      { id: "huggingface", label: t("local_models.tab_huggingface") },
      { id: "server", label: t("local_models.tab_server") },
    ];
    return mode === "advanced"
      ? all
      : all.filter((o) => !ADVANCED_ONLY.has(o.id));
  }, [mode, t]);

  return (
    <ScrollArea className="h-full">
      <div className="flex w-full flex-col gap-4 px-8 py-6">
        <BackLink
          label={t("local_models.back")}
          onClick={() => setActiveSection("apikeys")}
        />
        <PanelHeader
          title={t("local_models.title")}
          subtitle={t("local_models.subtitle")}
        />

        {/* The rail and the detail level belong together: the switch adds two
            areas to this very row (and detail to the rows below), so it sits
            beside what it changes instead of alone in the header. */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <SegmentedFilter<LocalModelsTab>
            label={t("local_models.tabs_label")}
            value={tab}
            onChange={setTab}
            options={tabs}
          />
          <div className="flex items-center gap-2">
            <span className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              {t("local_models.mode_label")}
            </span>
            <SegmentedFilter<LocalModelsMode>
              label={t("local_models.mode_label")}
              value={mode}
              onChange={changeMode}
              options={[
                { id: "simple", label: t("local_models.mode_simple") },
                { id: "advanced", label: t("local_models.mode_advanced") },
              ]}
            />
          </div>
        </div>

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
              onOpenAssistant={openAssistant}
              onReportProblem={reportProblem}
              advanced={mode === "advanced"}
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

        {providerId && tab === "assistant" && (
          <div data-testid="local-models-assistant-tab">
            <AssistantPanel
              providerId={providerId}
              serverLabel={descriptor?.label ?? "Ollama"}
              request={assistantRequest}
              onOpenApiKeys={openApiKeys}
              onOpenServerLog={openServerLog}
            />
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
            <ServerPanel
              providerId={providerId}
              initialLogOpen={serverLogOpen}
            />
          </div>
        )}
      </div>
    </ScrollArea>
  );
}
