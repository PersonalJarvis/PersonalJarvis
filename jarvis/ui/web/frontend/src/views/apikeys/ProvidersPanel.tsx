import { useEffect, useMemo, useState } from "react";
import { AlertCircle, Loader2, RefreshCw } from "lucide-react";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { Button } from "@/components/ui/button";
import {
  PROVIDER_BACKEND_UNREACHABLE,
  type ProviderDescriptor,
  type ProviderTier,
  type SectionHealth,
} from "@/hooks/useProviders";
import { useT } from "@/i18n";
import type { useProviderFamilies } from "@/lib/providerFamilies";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { FamilyDetail } from "./providers/FamilyDetail";
import { FamilyList } from "./providers/FamilyList";
import { familyState, type FamilyState, type FamilyUse } from "./providers/familyState";

type FamiliesData = ReturnType<typeof useProviderFamilies>;

/** The jobs the overview strip reports, each with the tier that answers it. */
const OVERVIEW: { use: FamilyUse; tier?: ProviderTier }[] = [
  { use: "voice", tier: "realtime" },
  { use: "agents" },
  { use: "speech", tier: "tts" },
  { use: "hearing", tier: "stt" },
];

/**
 * The provider page body: what runs where, then every company in a list with
 * the selected one's settings beside it.
 */
export function ProvidersPanel({
  data,
  providers,
  providersLoading,
  providersError,
  health,
  realtimeHealth,
  onProvidersChanged,
  onActivateOptimistic,
}: {
  data: FamiliesData;
  providers: ProviderDescriptor[];
  providersLoading: boolean;
  providersError: string | null;
  health: Record<string, SectionHealth | undefined>;
  realtimeHealth?: SectionHealth;
  onProvidersChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
}) {
  const t = useT();
  const { families, subscriptions, agents, error, reload } = data;
  const states = useMemo<Record<string, FamilyState>>(() => {
    const ctx = { providers, subscriptions, agents, health };
    return Object.fromEntries((families ?? []).map((f) => [f.id, familyState(f, ctx)]));
  }, [families, providers, subscriptions, agents, health]);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  // First visit: open the company that carries the voice, else the first one
  // that is connected — the page opens on something that is already set up.
  useEffect(() => {
    if (selectedId || !families?.length || !providers.length) return;
    const pick =
      families.find((f) => states[f.id]?.uses.includes("voice")) ??
      families.find((f) => states[f.id]?.connected && !f.local) ??
      families[0];
    setSelectedId(pick.id);
  }, [families, providers.length, selectedId, states]);

  const selected = families?.find((f) => f.id === selectedId) ?? null;
  const refreshAll = async () => {
    onProvidersChanged();
    await reload();
  };

  if (error === "restart_required" && !providersError) return <RestartNeeded />;

  if (providersError || error) {
    return (
      <div
        role="alert"
        className="flex items-start gap-2 rounded-xl border border-border bg-card px-5 py-4 text-sm text-destructive"
      >
        <AlertCircle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
        <p className="min-w-0 flex-1">
          {providersError === PROVIDER_BACKEND_UNREACHABLE
            ? t("apikeys_view.backend_unavailable")
            : t("providers_page.load_error")}
        </p>
        <Button variant="outline" size="sm" onClick={() => void refreshAll()}>
          {t("common.retry")}
        </Button>
      </div>
    );
  }

  if (!families || (providersLoading && !providers.length)) {
    return (
      <div role="status" className="flex items-center gap-2 px-1 py-6 text-sm text-muted-foreground">
        <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
        {t("providers_page.loading")}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-group">
      <Overview
        families={families}
        states={states}
        providers={providers}
        agents={agents}
        onSelect={setSelectedId}
      />

      <section aria-label={t("providers_page.list_label")} className="flex flex-col gap-3">
        <div className="px-1">
          <h2 className="text-lg font-semibold text-foreground-strong">{t("providers_page.list_title")}</h2>
          <p className="mt-0.5 text-base text-muted-foreground">{t("providers_page.list_desc")}</p>
        </div>
        <div
          data-testid="provider-master-detail"
          className="grid overflow-clip rounded-xl border border-border md:grid-cols-[18rem_minmax(0,1fr)]"
        >
          <div className="border-b border-border bg-card md:border-b-0 md:border-r">
            <div className="md:sticky md:top-0">
              <FamilyList
                families={families}
                states={states}
                selectedId={selectedId}
                onSelect={setSelectedId}
              />
            </div>
          </div>
          <div className="min-w-0 p-6">
            {selected && states[selected.id] && (
              <FamilyDetail
                key={selected.id}
                family={selected}
                state={states[selected.id]}
                agents={agents}
                realtimeHealth={realtimeHealth}
                onChanged={refreshAll}
                onProvidersChanged={onProvidersChanged}
                onActivateOptimistic={onActivateOptimistic}
              />
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

/**
 * The window runs a newer page than its backend: the bundle reloads on its
 * own after an update, the server does not. One sentence and one button,
 * instead of an error that suggests something is broken.
 */
function RestartNeeded() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [restarting, setRestarting] = useState(false);

  async function restart() {
    setRestarting(true);
    try {
      const response = await fetch("/api/settings/restart-app", { method: "POST" });
      if (response.status === 409) {
        pushToast("warning", t("topbar.restart_missions_running"));
        setRestarting(false);
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      // The window closes and relaunches; the button stays busy until then.
    } catch (cause) {
      pushToast("error", (cause as Error).message);
      setRestarting(false);
    }
  }

  return (
    <div
      role="status"
      data-testid="providers-restart-needed"
      className="flex items-center gap-4 rounded-xl border border-border bg-card px-5 py-4"
    >
      <RefreshCw aria-hidden="true" className="h-5 w-5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="text-base font-medium text-foreground-strong">{t("providers_page.restart_title")}</p>
        <p className="mt-0.5 text-sm text-muted-foreground">{t("providers_page.restart_desc")}</p>
      </div>
      <Button size="sm" disabled={restarting} onClick={() => void restart()}>
        {restarting && <Loader2 className="animate-spin" />}
        {t("providers_page.restart_action")}
      </Button>
    </div>
  );
}

/**
 * What runs where, in one row: the company behind each job right now. A click
 * opens that company below, where the job can be moved.
 */
function Overview({
  families,
  states,
  providers,
  agents,
  onSelect,
}: {
  families: NonNullable<FamiliesData["families"]>;
  states: Record<string, FamilyState>;
  providers: ProviderDescriptor[];
  agents: FamiliesData["agents"];
  onSelect: (id: string) => void;
}) {
  const t = useT();
  return (
    <div
      data-testid="provider-overview"
      className="grid grid-cols-2 overflow-hidden rounded-xl border border-border bg-card lg:grid-cols-4"
    >
      {OVERVIEW.map(({ use, tier }, index) => {
        const family = families.find((f) => states[f.id]?.uses.includes(use)) ?? null;
        const detail =
          use === "agents"
            ? agents?.mapping.find((row) => row.is_active_brain)?.label ?? null
            : providers.find((p) => p.tier === tier && p.active)?.label ?? null;
        return (
          <button
            key={use}
            type="button"
            data-testid={`provider-overview-${use}`}
            disabled={!family}
            onClick={() => family && onSelect(family.id)}
            className={cn(
              "flex min-w-0 items-center gap-3 px-5 py-4 text-left transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
              family && "hover:bg-secondary",
              index % 2 === 1 && "border-l border-border",
              index >= 2 && "border-t border-border lg:border-t-0",
              index === 2 && "lg:border-l",
            )}
          >
            {family ? (
              <ProviderLogo providerId={family.logo_id} label={family.label} />
            ) : (
              <span aria-hidden="true" className="h-9 w-9 shrink-0 rounded-md bg-secondary" />
            )}
            <span className="min-w-0">
              <span className="block text-sm text-muted-foreground">{t(`providers_page.use_${use}_long`)}</span>
              <span className="block truncate text-base font-medium text-foreground-strong">
                {family?.label ?? t("providers_page.overview_none")}
              </span>
              {family && detail && detail !== family.label && (
                <span className="block truncate text-sm text-muted-foreground">{detail}</span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}
