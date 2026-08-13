/**
 * Where the house gets connected — and the honest map of what is reachable.
 *
 * Two blocks, in the order the questions arrive:
 *
 * 1. **Your connections.** What is linked right now, whether it is answering,
 *    and — the field the maintainer actually cares about — how long it lasts.
 *    A connection that needs re-approving every seven days is not an
 *    integration, it is a chore, so the badge says so BEFORE anyone connects.
 * 2. **Every ecosystem.** Including the ones that cannot be reached at all.
 *    "Google's Home APIs are mobile-only" is an answer people are searching
 *    for; hiding those cards would just make the search take longer.
 *
 * Connecting a hub reuses the marketplace's existing connect dialog rather than
 * growing a second one here. The user connected Home Assistant once; asking
 * them to do it again on a different screen is the duplicated setup this
 * section exists to remove.
 */
import { useState } from "react";
import {
  CheckCircle2,
  CircleSlash,
  ExternalLink,
  Loader2,
  Plug,
  TriangleAlert,
  WifiOff,
} from "lucide-react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import {
  setDemoMode,
  useEcosystems,
  type ConnectionState,
  type Ecosystem,
  type ProviderStatus,
} from "@/hooks/useSmartHome";

export interface ConnectionsTabProps {
  providers: ProviderStatus[];
  onRefresh: () => void;
}

const STATE_STYLE: Record<
  ConnectionState,
  { dot: string; icon: typeof CheckCircle2; labelKey: string }
> = {
  connected: {
    dot: "bg-emerald-400",
    icon: CheckCircle2,
    labelKey: "smarthome.state_connected",
  },
  not_configured: {
    dot: "bg-muted-foreground/50",
    icon: Plug,
    labelKey: "smarthome.state_not_configured",
  },
  needs_reauth: {
    dot: "bg-amber-400",
    icon: TriangleAlert,
    labelKey: "smarthome.state_needs_reauth",
  },
  unreachable: {
    dot: "bg-amber-400",
    icon: WifiOff,
    labelKey: "smarthome.state_unreachable",
  },
};

const REACHABILITY_STYLE: Record<Ecosystem["reachability"], string> = {
  direct: "border-emerald-500/40 text-emerald-500",
  via_hub: "border-primary/40 text-primary",
  planned: "border-border text-muted-foreground",
  unavailable: "border-border text-muted-foreground/70",
};

export function ConnectionsTab({ providers, onRefresh }: ConnectionsTabProps) {
  const t = useT();
  const setActive = useEventStore((s) => s.setActiveSection);
  const { ecosystems, loading: ecoLoading } = useEcosystems();
  const demoProvider = providers.find((p) => p.provider === "demo");
  const [demoBusy, setDemoBusy] = useState(false);

  const toggleDemo = async (enabled: boolean) => {
    setDemoBusy(true);
    try {
      await setDemoMode(enabled);
      onRefresh();
    } finally {
      setDemoBusy(false);
    }
  };

  return (
    <ScrollArea className="h-full">
      <div className="space-y-8 p-6">
        <section>
          <h3 className="mb-1 font-display text-sm font-semibold">
            {t("smarthome.connections.title")}
          </h3>
          <p className="mb-4 text-xs text-muted-foreground">
            {t("smarthome.connections.subtitle")}
          </p>
          <div className="grid gap-3 lg:grid-cols-2">
            {providers
              .filter((p) => p.provider !== "demo")
              .map((provider) => {
                const style = STATE_STYLE[provider.state] ?? STATE_STYLE.unreachable;
                const Icon = style.icon;
                return (
                  <div
                    key={provider.provider}
                    data-testid={`smarthome-provider-${provider.provider}`}
                    className="rounded-xl border border-border bg-card p-4 text-card-foreground"
                  >
                    <div className="flex items-start gap-3">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border bg-secondary/50">
                        <Icon className="h-4 w-4 text-muted-foreground" aria-hidden />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium">
                            {provider.display_name}
                          </span>
                          <span
                            className={cn("h-1.5 w-1.5 rounded-full", style.dot)}
                            aria-hidden
                          />
                          <span className="text-xs text-muted-foreground">
                            {t(style.labelKey)}
                          </span>
                        </div>
                        {provider.detail && (
                          <p className="mt-1 text-xs text-muted-foreground">
                            {provider.detail}
                          </p>
                        )}
                        <LongevityBadge longevity={provider.longevity} />
                      </div>
                    </div>
                    {provider.state !== "connected" && (
                      <button
                        type="button"
                        onClick={() => setActive("plugins")}
                        className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
                      >
                        {t("smarthome.connections.connect")}
                        <ExternalLink className="h-3 w-3" aria-hidden />
                      </button>
                    )}
                    {provider.state === "connected" &&
                      provider.device_count !== null && (
                        <p className="mt-3 text-xs text-muted-foreground">
                          {t("smarthome.connections.device_count").replace(
                            "{count}",
                            String(provider.device_count),
                          )}
                        </p>
                      )}
                  </div>
                );
              })}

            {/* The demo home is a connection like any other, so it sits in the
                same grid — but labelled, always, as simulated. */}
            <div className="rounded-xl border border-dashed border-border bg-card p-4 text-card-foreground">
              <div className="flex items-start gap-3">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border bg-secondary/50">
                  <CircleSlash className="h-4 w-4 text-muted-foreground" aria-hidden />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">
                      {t("smarthome.demo.title")}
                    </span>
                    {demoBusy ? (
                      <Loader2
                        className="h-3.5 w-3.5 animate-spin text-muted-foreground"
                        aria-hidden
                      />
                    ) : (
                      <Switch
                        checked={Boolean(demoProvider)}
                        aria-label={t("smarthome.demo.title")}
                        onCheckedChange={(next) => void toggleDemo(next)}
                      />
                    )}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t("smarthome.demo.body")}
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section>
          <h3 className="mb-1 font-display text-sm font-semibold">
            {t("smarthome.ecosystems.title")}
          </h3>
          <p className="mb-4 text-xs text-muted-foreground">
            {t("smarthome.ecosystems.subtitle")}
          </p>
          {ecoLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden />
          ) : (
            <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
              {ecosystems.map((eco) => (
                <EcosystemCard key={eco.id} ecosystem={eco} />
              ))}
            </div>
          )}
        </section>
      </div>
    </ScrollArea>
  );
}

function LongevityBadge({ longevity }: { longevity: string }) {
  const t = useT();
  const key =
    longevity === "permanent"
      ? "smarthome.longevity.permanent"
      : longevity === "self_renewing"
        ? "smarthome.longevity.self_renewing"
        : "smarthome.longevity.provider_limited";
  const tone =
    longevity === "permanent"
      ? "border-emerald-500/40 text-emerald-500"
      : longevity === "self_renewing"
        ? "border-border text-muted-foreground"
        : "border-amber-500/40 text-amber-500";
  return (
    <span
      className={cn(
        "mt-2 inline-flex rounded-full border px-2 py-0.5 text-[10px] font-medium",
        tone,
      )}
    >
      {t(key)}
    </span>
  );
}

function EcosystemCard({ ecosystem }: { ecosystem: Ecosystem }) {
  const t = useT();
  const [logoFailed, setLogoFailed] = useState(false);
  const reachKey = `smarthome.reach.${ecosystem.reachability}`;

  return (
    <div
      data-testid={`smarthome-ecosystem-${ecosystem.id}`}
      className={cn(
        "rounded-xl border border-border bg-card p-4 text-card-foreground",
        ecosystem.reachability === "unavailable" && "opacity-70",
      )}
    >
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-border bg-secondary/50">
          {logoFailed ? (
            <span className="text-xs font-semibold text-muted-foreground">
              {ecosystem.display_name.slice(0, 1)}
            </span>
          ) : (
            /* Monochrome on purpose: the CDN renders the mark in one colour,
               and `currentColor` is not available to an <img>, so a neutral
               grey is the one value that reads correctly in BOTH themes. */
            <img
              src={`https://cdn.simpleicons.org/${ecosystem.logo_slug}/9CA3AF`}
              alt=""
              width={18}
              height={18}
              onError={() => setLogoFailed(true)}
            />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{ecosystem.display_name}</span>
            <span
              className={cn(
                "rounded-full border px-2 py-0.5 text-[10px] font-medium",
                REACHABILITY_STYLE[ecosystem.reachability],
              )}
            >
              {t(reachKey)}
            </span>
          </div>
          {ecosystem.covers !== "—" && (
            <p className="mt-1 text-xs text-muted-foreground">{ecosystem.covers}</p>
          )}
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground/80">
            {ecosystem.note}
          </p>
        </div>
      </div>
    </div>
  );
}
