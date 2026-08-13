/**
 * Where the house gets connected — one recommended route, then the brands.
 *
 * The old version put twenty equal cards in one flat grid, each a plain `<div>`
 * with no click handler, every logo in the same flat grey. Three things were
 * wrong with that at once, and all three were structural rather than cosmetic:
 *
 * 1. **It did not answer the question.** "Does this work with my stuff?" has
 *    one answer for almost every house — connect Home Assistant, because it
 *    already speaks to nearly every brand in the list. Ranking that entry level
 *    with nineteen others hid the answer inside the noise.
 * 2. **Nothing was clickable.** Cards that look pressable and do nothing are
 *    worse than plain text, because the reader spends attention finding out.
 * 3. **Nothing was recognisable.** One uniform grey means every card has to be
 *    READ. A brand mark on the brand's colour is recognised before the name is,
 *    which is the whole reason logos exist.
 *
 * So: a hero for the hub, then the names people own, and the protocols folded
 * away behind one toggle. The technical entries are not deleted — someone
 * running Zigbee2MQTT still needs to find it — they simply do not stand between
 * a beginner and the thing that will actually work for them.
 */
import { useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  CircleSlash,
  Loader2,
  Plug,
  Sparkles,
  TriangleAlert,
  WifiOff,
} from "lucide-react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import {
  setDemoMode,
  useEcosystems,
  type ConnectionState,
  type Ecosystem,
  type ProviderStatus,
} from "@/hooks/useSmartHome";
import { BrandMark } from "@/views/smarthome/BrandMark";
import { EcosystemSheet } from "@/views/smarthome/EcosystemSheet";

export interface ConnectionsTabProps {
  providers: ProviderStatus[];
  onRefresh: () => void;
}

const STATE_STYLE: Record<
  ConnectionState,
  { dot: string; icon: typeof CheckCircle2; labelKey: string }
> = {
  connected: { dot: "bg-emerald-400", icon: CheckCircle2, labelKey: "smarthome.state_connected" },
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
  unreachable: { dot: "bg-amber-400", icon: WifiOff, labelKey: "smarthome.state_unreachable" },
};

const REACHABILITY_STYLE: Record<Ecosystem["reachability"], string> = {
  direct: "border-emerald-500/40 text-emerald-500",
  via_hub: "border-primary/40 text-primary",
  planned: "border-border text-muted-foreground",
  unavailable: "border-border text-muted-foreground/70",
};

/**
 * Split the list into hero / shown / folded away — tolerating a backend that
 * has never heard of tiers.
 *
 * The desktop app's Python side does NOT hot-reload, so a freshly built
 * frontend routinely runs for a while against an older server. If this filtered
 * strictly on `tier === "popular"`, that pairing would render an EMPTY brand
 * grid — a section that looks far more broken than the one being replaced.
 *
 * So the rule is the same tolerance the device vocabulary already uses: only an
 * EXPLICIT "technical" is folded away, anything unknown or missing stays
 * visible. Worst case the reader sees the full list in the new layout; nothing
 * ever disappears.
 */
function partition(ecosystems: Ecosystem[]): {
  hub: Ecosystem | null;
  popular: Ecosystem[];
  technical: Ecosystem[];
} {
  const hub =
    ecosystems.find((e) => e.tier === "hub") ??
    // Older server: recognise the hub by identity rather than losing the hero.
    ecosystems.find((e) => e.id === "home_assistant") ??
    null;
  const rest = ecosystems.filter((e) => e.id !== hub?.id);
  return {
    hub,
    popular: rest.filter((e) => e.tier !== "technical"),
    technical: rest.filter((e) => e.tier === "technical"),
  };
}

export function ConnectionsTab({ providers, onRefresh }: ConnectionsTabProps) {
  const t = useT();
  const { ecosystems, loading: ecoLoading } = useEcosystems();
  const [showAll, setShowAll] = useState(false);
  const [opened, setOpened] = useState<Ecosystem | null>(null);
  const [demoBusy, setDemoBusy] = useState(false);

  const demoProvider = providers.find((p) => p.provider === "demo");
  const hubStatus = providers.find((p) => p.provider === "home_assistant");

  const { hub, popular, technical } = useMemo(
    () => partition(ecosystems),
    [ecosystems],
  );

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
    <>
      <ScrollArea className="h-full">
        <div className="space-y-8 p-6">
          {ecoLoading ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden />
          ) : (
            <>
              {hub && (
                <HubHero
                  ecosystem={hub}
                  status={hubStatus}
                  onOpen={() => setOpened(hub)}
                />
              )}

              <section>
                <SectionHead
                  title={t("smarthome.eco.popular_title")}
                  subtitle={t("smarthome.eco.popular_subtitle")}
                />
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {popular.map((eco) => (
                    <EcosystemCard
                      key={eco.id}
                      ecosystem={eco}
                      onOpen={() => setOpened(eco)}
                    />
                  ))}
                </div>
              </section>

              {/* Hidden entirely when nothing is folded away — a disclosure
                  that opens onto nothing is a promise the screen breaks. */}
              <section className={cn(technical.length === 0 && "hidden")}>
                <button
                  type="button"
                  onClick={() => setShowAll((prev) => !prev)}
                  data-testid="smarthome-show-all-ecosystems"
                  aria-expanded={showAll}
                  className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
                >
                  <ChevronDown
                    className={cn("h-3.5 w-3.5 transition-transform", showAll && "rotate-180")}
                    aria-hidden
                  />
                  {showAll
                    ? t("smarthome.eco.hide_technical")
                    : t("smarthome.eco.show_technical").replace(
                        "{count}",
                        String(technical.length),
                      )}
                </button>
                {showAll && (
                  <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {technical.map((eco) => (
                      <EcosystemCard
                        key={eco.id}
                        ecosystem={eco}
                        onOpen={() => setOpened(eco)}
                      />
                    ))}
                  </div>
                )}
              </section>

              <section>
                <SectionHead
                  title={t("smarthome.demo.title")}
                  subtitle={t("smarthome.demo.body")}
                />
                <div className="flex items-center gap-3 rounded-xl border border-dashed border-border bg-card p-4">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border bg-secondary/50">
                    <CircleSlash className="h-4 w-4 text-muted-foreground" aria-hidden />
                  </span>
                  <p className="min-w-0 flex-1 text-xs text-muted-foreground">
                    {t("smarthome.demo.hint")}
                  </p>
                  {demoBusy ? (
                    <Loader2
                      className="h-4 w-4 animate-spin text-muted-foreground"
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
              </section>
            </>
          )}
        </div>
      </ScrollArea>

      <EcosystemSheet
        ecosystem={opened}
        onOpenChange={(open) => !open && setOpened(null)}
        hubStatus={hubStatus}
        onConnected={onRefresh}
        onOpenHub={() => setOpened(hub)}
      />
    </>
  );
}

function SectionHead({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="mb-3">
      <h3 className="font-display text-sm font-semibold">{title}</h3>
      <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>
    </div>
  );
}

/**
 * Home Assistant, given the weight it actually carries.
 *
 * For nearly every house this one card IS the answer, so it is drawn as a
 * recommendation and not as the first of twenty peers: full width, the brand's
 * own tint behind it, its live connection state on the face, and the number
 * that makes the case — around 2000 brands through one connection.
 */
function HubHero({
  ecosystem,
  status,
  onOpen,
}: {
  ecosystem: Ecosystem;
  status: ProviderStatus | undefined;
  onOpen: () => void;
}) {
  const t = useT();
  const state = status?.state ?? "not_configured";
  const style = STATE_STYLE[state] ?? STATE_STYLE.unreachable;
  const connected = state === "connected";

  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid="smarthome-hub-hero"
      className={cn(
        "group relative flex w-full items-start gap-4 overflow-hidden rounded-2xl border p-5 text-left transition-colors",
        connected ? "border-emerald-500/40" : "border-primary/40",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "pointer-events-none absolute inset-0 bg-gradient-to-br to-transparent",
          connected ? "from-emerald-500/[0.10]" : "from-primary/[0.10]",
        )}
      />
      <BrandMark
        id={ecosystem.id}
        name={ecosystem.display_name}
        logoSlug={ecosystem.logo_slug}
        logoColor={ecosystem.logo_color}
        size="lg"
        className="relative"
      />
      <span className="relative min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-display text-base font-semibold text-foreground">
            {ecosystem.display_name}
          </span>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
            <Sparkles className="h-3 w-3" aria-hidden />
            {t("smarthome.eco.recommended")}
          </span>
          <span className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span className={cn("h-1.5 w-1.5 rounded-full", style.dot)} aria-hidden />
            {t(style.labelKey)}
          </span>
        </span>
        <span className="mt-1.5 block text-sm leading-relaxed text-muted-foreground">
          {t("smarthome.eco.hub_pitch")}
        </span>
        <span className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors group-hover:bg-primary/20">
          {connected
            ? t("smarthome.eco.manage")
            : t("smarthome.connections.connect")}
        </span>
        {connected && status?.device_count != null && (
          <span className="ml-2 text-xs text-muted-foreground">
            {t("smarthome.connections.device_count").replace(
              "{count}",
              String(status.device_count),
            )}
          </span>
        )}
      </span>
    </button>
  );
}

/** One brand, pressable, with the mark doing the recognising. */
function EcosystemCard({
  ecosystem,
  onOpen,
}: {
  ecosystem: Ecosystem;
  onOpen: () => void;
}) {
  const t = useT();
  const unreachable = ecosystem.reachability === "unavailable";

  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid={`smarthome-ecosystem-${ecosystem.id}`}
      className={cn(
        "flex items-start gap-3 rounded-xl border border-border bg-card p-4 text-left transition-colors",
        "hover:border-primary/40 focus-visible:border-primary/40",
        unreachable && "opacity-70",
      )}
    >
      <BrandMark
        id={ecosystem.id}
        name={ecosystem.display_name}
        logoSlug={ecosystem.logo_slug}
        logoColor={ecosystem.logo_color}
        size="sm"
        dimmed={unreachable}
      />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">
            {ecosystem.display_name}
          </span>
          <span
            className={cn(
              "rounded-full border px-2 py-0.5 text-[10px] font-medium",
              REACHABILITY_STYLE[ecosystem.reachability],
            )}
          >
            {t(`smarthome.reach.${ecosystem.reachability}`)}
          </span>
        </span>
        {/* One line, clamped. The full text lives in the sheet — a card that
            carries four lines of prose is the wall this section had. */}
        {ecosystem.covers !== "—" && (
          <span className="mt-1 line-clamp-2 block text-xs leading-relaxed text-muted-foreground">
            {ecosystem.covers}
          </span>
        )}
      </span>
    </button>
  );
}
