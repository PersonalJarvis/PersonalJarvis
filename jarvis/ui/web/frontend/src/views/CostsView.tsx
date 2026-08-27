/**
 * Spend & Tokens — where the money and the context window actually went.
 *
 * Same settings surface as Skills, Plugins, MCPs and CLIs: one quiet column of
 * panels built from `components/extensions/primitives`, so this section cannot
 * drift into a look of its own. What it adds on top is a drill-down: every
 * bar, every breakdown row and every line item is a filter. Clicking
 * "gemini-live" in the provider table narrows the totals, the chart, the
 * models table and the line items in one move, and the active filters sit as
 * removable chips under the header.
 *
 * The numbers come from `/api/costs` — a read model over the stores the app
 * already writes (voice sessions, missions, agent chats). Nothing here is a
 * separate ledger that could disagree with them.
 */
import { useMemo, useState } from "react";
import {
  AlertTriangle,
  AudioLines,
  CalendarDays,
  ChevronDown,
  ChevronRight,
  Coins,
  CreditCard,
  Hash,
  Layers,
  RefreshCw,
  Search,
  Sigma,
  Sparkles,
  Tag,
  Terminal,
  Ticket,
  Wallet,
  X,
} from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import {
  BackLink,
  Cell,
  Column,
  DetailHeader,
  EmptyRow,
  IconButton,
  InlineSearch,
  Panel,
  PanelHeader,
  SegmentedFilter,
  StatTile,
  StatusDot,
  Table,
  TableHead,
  TableRow,
} from "@/components/extensions/primitives";
import { CostTrendChart, type TrendMetric } from "@/components/costs/CostTrendChart";
import {
  dayBoundsMs,
  formatBucketFull,
  formatClock,
  formatDayLong,
  formatDayShort,
  formatExact,
  formatMoney,
  formatShare,
  formatTimestamp,
  formatTokens,
  formatTokensOrNone,
  keyColor,
  priceSourceLabelKey,
  priceSourceTone,
  relativeDay,
  roleColor,
  roleLabelKey,
  SURFACE_GROUPS,
  surfaceGroupFor,
  surfaceGroupLabelKey,
  surfaceLabelKey,
} from "@/components/costs/costFormat";
import {
  EMPTY_FILTERS,
  hasActiveFilters,
  useCostDaily,
  useCostEntries,
  useCostPricing,
  useCostSummary,
  type CostBucket,
  type CostDayRow,
  type CostFilters,
  type CostRole,
  type CostSurface,
} from "@/hooks/useCosts";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

type Dimension = "provider" | "model" | "role" | "surface";
type Currency = "usd" | "eur";
type EntrySort = "recent" | "cost" | "tokens";

const RANGES = [7, 30, 90, 0] as const;
const ENTRY_PAGE = 50;

export function CostsView() {
  const t = useT();
  const [filters, setFilters] = useState<CostFilters>(EMPTY_FILTERS);
  const [currency, setCurrency] = useState<Currency>("usd");
  const [metric, setMetric] = useState<TrendMetric>("cost");
  const [dimension, setDimension] = useState<Dimension>("provider");
  const [searchOpen, setSearchOpen] = useState(false);
  const [ratesOpen, setRatesOpen] = useState(false);
  /** Which day's report is open, as a local `YYYY-MM-DD`. Null is the ledger. */
  const [openDay, setOpenDay] = useState<string | null>(null);

  const summary = useCostSummary(filters);
  const daily = useCostDaily(filters);
  const pricing = useCostPricing(filters.days, ratesOpen);

  const data = summary.data;
  const eurPerUsd = data?.currency.eur_per_usd ?? 0.92;
  const totals = data?.totals;
  const money = (usd: number) => formatMoney(usd, eurPerUsd, currency);

  // One toggle for every filter dimension: clicking a row that is already
  // filtered removes it again, so a drill-down is never a one-way door.
  const toggle = (key: keyof CostFilters, value: string) => {
    setFilters((f) => {
      const list = f[key] as string[];
      const next = list.includes(value)
        ? list.filter((v) => v !== value)
        : [...list, value];
      return { ...f, [key]: next };
    });
  };

  const rows: CostBucket[] = useMemo(() => {
    if (!data) return [];
    switch (dimension) {
      case "model":
        return data.by_model;
      case "role":
        return data.by_role;
      case "surface":
        return data.by_surface;
      default:
        return data.by_provider;
    }
  }, [data, dimension]);

  const filterKeyFor = (d: Dimension): keyof CostFilters =>
    d === "provider" ? "providers" : d === "model" ? "models" : d === "role" ? "roles" : "surfaces";

  const activeFor = (d: Dimension): string[] => filters[filterKeyFor(d)] as string[];

  // The switch is not extra state: it reads the surface filter and writes it.
  // Anything else would let a row click and the switch disagree about which
  // area is on screen.
  const area = surfaceGroupFor(filters.surfaces);
  const pickArea = (id: string) => {
    const group = SURFACE_GROUPS.find((g) => g.id === id);
    if (!group) return;
    // Every row-level filter is cleared with the area, not just roles: a
    // provider picked inside Agentic IDE ("claude-cli") carried over to the
    // assistant's area, where no row can ever match it, and the tab showed
    // "0 calls" as if nothing had been spent (maintainer, 2026-08-25). An
    // area switch is a fresh question; only the time range and the search
    // box survive it.
    setFilters((f) => ({
      ...f,
      surfaces: [...group.surfaces],
      roles: [],
      providers: [],
      models: [],
      refs: [],
    }));
  };

  const topSpender = data?.by_provider[0];
  const rangeMs = Math.max(1, (data?.until_ms ?? 0) - (data?.since_ms ?? 0));
  // `cost_usd` is what the work was WORTH; the seat-covered part of it never
  // reached an invoice. Splitting them here keeps every downstream number
  // honest about which question it answers.
  const seat = totals?.subscription_usd ?? 0;
  const billed = Math.max(0, (totals?.cost_usd ?? 0) - seat);
  const perDay = billed / Math.max(1, rangeMs / 86_400_000);

  // A day report replaces the section rather than sitting inside it: it is
  // the same question at a different grain, and two scrollers on one screen
  // is how a reader loses track of which totals belong to what.
  if (openDay) {
    return (
      <DayReport
        date={openDay}
        filters={filters}
        currency={currency}
        eurPerUsd={eurPerUsd}
        onBack={() => setOpenDay(null)}
      />
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="mx-auto flex max-w-[1180px] flex-col gap-4 px-6 py-6">
        <PanelHeader
          title={t("costs_view.title")}
          subtitle={
            data
              ? fill(t("costs_view.subtitle"), {
                  entries: formatExact(totals?.entries ?? 0),
                  models: String(data.models.length),
                  // Both counts come from the FILTERED report — mixing in the
                  // unfiltered facet count would claim eight providers next
                  // to two models while a provider filter is on.
                  providers: String(data.by_provider.length),
                })
              : t("costs_view.subtitle_loading")
          }
          actions={
            <>
              <SegmentedFilter<string>
                label={t("costs_view.range_label")}
                value={String(filters.days)}
                onChange={(v) => setFilters((f) => ({ ...f, days: Number(v) }))}
                options={RANGES.map((d) => ({
                  id: String(d),
                  label: d === 0 ? t("costs_view.range_all") : fill(t("costs_view.range_days"), { days: String(d) }),
                }))}
              />
              <span className="mx-1 h-4 w-px bg-border" aria-hidden />
              <SegmentedFilter<Currency>
                label={t("costs_view.currency_label")}
                value={currency}
                onChange={setCurrency}
                options={[
                  { id: "usd", label: "USD" },
                  { id: "eur", label: "EUR" },
                ]}
              />
              <IconButton
                label={t("costs_view.search")}
                active={searchOpen}
                onClick={() => setSearchOpen((v) => !v)}
              >
                <Search className="h-4 w-4" />
              </IconButton>
              <IconButton
                label={t("costs_view.refresh")}
                busy={summary.isFetching}
                onClick={() => void summary.refetch()}
              >
                <RefreshCw className="h-4 w-4" />
              </IconButton>
            </>
          }
        />

        {/* ---------------------------------------------------------------
            Which part of the app you are looking at. Roles answer "what kind
            of model"; this answers "where" — and the two together are the
            question people actually ask ("what does the coding agent cost me").
        --------------------------------------------------------------- */}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
          <AreaSwitch value={area?.id ?? null} onChange={pickArea} />
          <RoleFilters
            roles={(area ?? SURFACE_GROUPS[0]).roles}
            available={data?.facets.roles ?? []}
            active={filters.roles}
            onToggle={(role) => toggle("roles", role)}
          />
        </div>

        {searchOpen ? (
          <div className="max-w-sm">
            <InlineSearch
              autoFocus
              value={filters.search}
              onChange={(v) => setFilters((f) => ({ ...f, search: v }))}
              placeholder={t("costs_view.search_placeholder")}
            />
          </div>
        ) : null}

        <ActiveFilterChips
          filters={filters}
          labelFor={(value) =>
            data?.top_refs.find((r) => r.key === value)?.label || `${value.slice(0, 8)}…`
          }
          onRemove={(key, value) => toggle(key, value)}
          onClear={() => setFilters((f) => ({ ...EMPTY_FILTERS, days: f.days }))}
        />

        {summary.isError ? (
          <EmptyRow>{t("costs_view.load_error")}</EmptyRow>
        ) : null}

        {/*
          The coding-CLI numbers come from an index that fills in over
          background runs — a first start, a new account, a re-read after a
          rule change. A page that shows a half-read index as the month's
          total reads as money gone missing ($8 000 next to yesterday's
          $12 300, 2026-08-27). Say it is still counting, and how far it is.
        */}
        {data?.index && !data.index.complete ? (
          <EmptyRow>
            {fill(t("costs_view.index_catching_up"), {
              indexed: formatExact(data.index.files_indexed),
              known: formatExact(data.index.files_known),
              pendingMb: formatExact(Math.round(data.index.bytes_pending / 1_000_000)),
            })}
          </EmptyRow>
        ) : null}

        {/*
          An empty report under a row filter is indistinguishable from
          "nothing was spent" — the tab looked broken for a day because a
          provider picked in another area was still on (2026-08-25). Name
          the cause and offer the one click that fixes it.
        */}
        {data &&
        !summary.isLoading &&
        (totals?.entries ?? 0) === 0 &&
        (filters.providers.length > 0 ||
          filters.models.length > 0 ||
          filters.roles.length > 0 ||
          filters.refs.length > 0) ? (
          <EmptyRow>
            <span>{t("costs_view.empty_filtered")}</span>{" "}
            <button
              type="button"
              className="underline underline-offset-2 hover:text-foreground"
              onClick={() =>
                setFilters((f) => ({ ...f, providers: [], models: [], roles: [], refs: [] }))
              }
            >
              {t("costs_view.empty_filtered_action")}
            </button>
          </EmptyRow>
        ) : null}

        {/* ---------------------------------------------------------------
            Headline numbers. Cost and tokens sit side by side on purpose:
            the cheapest provider is often the loudest token consumer, and
            one number without the other hides that.
        --------------------------------------------------------------- */}
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {/*
            Everything the work was worth at API list price — metered calls
            AND the seat-covered ones. Showing only the metered part put
            "$18" over a table that summed to six thousand and read as a lie
            (maintainer, 2026-08-25). The split — what an API key was billed
            vs. what a subscription covered — is the line below, which is the
            answer to "did money leave the account".
          */}
          <StatTile
            icon={<Coins className="h-4 w-4" />}
            label={t("costs_view.stat_total")}
            value={money(totals?.cost_usd ?? 0)}
            hint={
              seat > 0 && billed <= 0
                ? t("costs_view.stat_total_hint_seat_only")
                : seat > 0
                  ? fill(t("costs_view.stat_total_hint_seat"), {
                      billed: money(billed),
                      seat: money(seat),
                    })
                  : fill(t("costs_view.stat_total_hint"), { perDay: money(perDay) })
            }
            loading={summary.isLoading}
          />
          {/*
            The headline is in + cached + out. Cache reads bill at their own
            rate, so the read model keeps them out of `tokens_in` — naming only
            two of the three buckets made the tile look like it could not add
            up. The cached term appears only when there is cache to report.
          */}
          <StatTile
            icon={<Sigma className="h-4 w-4" />}
            label={t("costs_view.stat_tokens")}
            value={formatTokens(totals?.tokens_total ?? 0)}
            hint={fill(
              t(
                (totals?.tokens_cached ?? 0) > 0
                  ? "costs_view.stat_tokens_hint_cached"
                  : "costs_view.stat_tokens_hint",
              ),
              {
                in: formatTokens(totals?.tokens_in ?? 0),
                cached: formatTokens(totals?.tokens_cached ?? 0),
                out: formatTokens(totals?.tokens_out ?? 0),
              },
            )}
            loading={summary.isLoading}
          />
          <StatTile
            icon={<Tag className="h-4 w-4" />}
            label={t("costs_view.stat_top_provider")}
            value={topSpender?.key ?? "—"}
            hint={
              topSpender
                ? fill(t("costs_view.stat_top_provider_hint"), {
                    amount: money(topSpender.cost_usd),
                    share: formatShare(topSpender.cost_share),
                  })
                : t("costs_view.stat_none")
            }
            loading={summary.isLoading}
          />
          <StatTile
            icon={<AlertTriangle className="h-4 w-4" />}
            label={t("costs_view.stat_gap")}
            // A gap in the speech layer spends characters, not tokens, so
            // its token count is a truthful zero. Fall back to naming the
            // calls, or the tile would report "0 unpriced" while warning.
            value={
              (totals?.gap_tokens ?? 0) > 0
                ? formatTokens(totals?.gap_tokens ?? 0)
                : formatExact(totals?.gap_entries ?? 0)
            }
            hint={
              (totals?.gap_tokens ?? 0) > 0
                ? fill(t("costs_view.stat_gap_hint"), {
                    entries: formatExact(totals?.gap_entries ?? 0),
                  })
                : t("costs_view.stat_gap_none")
            }
            tone={(totals?.gap_tokens ?? 0) > 0 ? "warn" : "ok"}
            loading={summary.isLoading}
          />
        </div>

        {/* Trend ------------------------------------------------------- */}
        <Panel className="p-4">
          <PanelHeader
            title={t("costs_view.trend_title")}
            subtitle={
              data
                ? fill(t("costs_view.trend_subtitle"), {
                    from: formatBucketFull(data.series[0]?.key ?? "", data.bucket),
                    to: formatBucketFull(data.series[data.series.length - 1]?.key ?? "", data.bucket),
                  })
                : ""
            }
            actions={
              <SegmentedFilter<TrendMetric>
                label={t("costs_view.metric_label")}
                value={metric}
                onChange={setMetric}
                options={[
                  { id: "cost", label: t("costs_view.metric_cost") },
                  { id: "tokens", label: t("costs_view.metric_tokens") },
                ]}
              />
            }
          />
          <div className="mt-3 h-[220px]">
            <CostTrendChart
              series={data?.series ?? []}
              bucket={data?.bucket ?? "day"}
              metric={metric}
              currency={currency}
              eurPerUsd={eurPerUsd}
            />
          </div>
        </Panel>

        {/* Breakdown --------------------------------------------------- */}
        <Panel>
          <div className="px-4 pt-4">
            <PanelHeader
              title={t("costs_view.breakdown_title")}
              subtitle={t("costs_view.breakdown_subtitle")}
              actions={
                <SegmentedFilter<Dimension>
                  label={t("costs_view.dimension_label")}
                  value={dimension}
                  onChange={setDimension}
                  options={[
                    { id: "provider", label: t("costs_view.dim_provider") },
                    { id: "model", label: t("costs_view.dim_model") },
                    { id: "role", label: t("costs_view.dim_role") },
                    { id: "surface", label: t("costs_view.dim_surface") },
                  ]}
                />
              }
            />
          </div>
          <div className="mt-3">
            <BreakdownTable
              rows={rows}
              dimension={dimension}
              active={activeFor(dimension)}
              onToggle={(key) => toggle(filterKeyFor(dimension), key)}
              money={money}
              loading={summary.isLoading}
            />
          </div>
        </Panel>

        {/* Speech models — the speech area only ------------------------- */}
        {area?.id === "jarvis-voice" ? (
          <Panel>
            <div className="px-4 pt-4">
              <p className="mb-3 text-xs leading-relaxed text-muted-foreground">
                {t("costs_view.speech_area_note")}
              </p>
              <PanelHeader
                title={t("costs_view.speech_models_title")}
                subtitle={t("costs_view.speech_models_subtitle")}
              />
            </div>
            <div className="mt-3">
              <SpeechModelsTable
                rows={data?.by_model ?? []}
                money={money}
                loading={summary.isLoading}
              />
            </div>
          </Panel>
        ) : null}

        {/* Where it went ----------------------------------------------- */}
        <Panel>
          <div className="px-4 pt-4">
            <PanelHeader
              title={t("costs_view.top_refs_title")}
              subtitle={t("costs_view.top_refs_subtitle")}
            />
          </div>
          <div className="mt-3">
            <TopRefsTable
              rows={data?.top_refs ?? []}
              active={filters.refs}
              onToggle={(key) => toggle("refs", key)}
              money={money}
              loading={summary.isLoading}
            />
          </div>
        </Panel>

        {/* The daily ledger -------------------------------------------- */}
        <Panel>
          <div className="px-4 pt-4">
            <PanelHeader
              title={t("costs_view.daily_title")}
              subtitle={t("costs_view.daily_subtitle")}
            />
          </div>
          <div className="mt-3">
            <DailyTable
              rows={daily.data?.days ?? []}
              money={money}
              loading={daily.isLoading}
              onOpen={setOpenDay}
            />
          </div>
        </Panel>

        {/* Rate card --------------------------------------------------- */}
        <Panel>
          <button
            type="button"
            onClick={() => setRatesOpen((v) => !v)}
            aria-expanded={ratesOpen}
            className="flex w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-sheen/[0.04]"
          >
            <Layers className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium text-foreground">{t("costs_view.rates_title")}</span>
            <span className="text-xs text-muted-foreground">{t("costs_view.rates_subtitle")}</span>
            <ChevronDown
              className={cn(
                "ml-auto h-4 w-4 text-muted-foreground transition-transform",
                ratesOpen && "rotate-180",
              )}
            />
          </button>
          {ratesOpen ? (
            <RatesTable
              rates={pricing.data?.rates ?? []}
              currency={currency}
              eurPerUsd={eurPerUsd}
              loading={pricing.isLoading}
            />
          ) : null}
        </Panel>

        <p className="px-1 pb-2 text-[11px] leading-relaxed text-muted-foreground">
          {fill(t("costs_view.footnote"), {
            sources: (data?.sources_present ?? []).join(", ") || "—",
            rate: eurPerUsd.toFixed(2),
            rateSource: t(
              data?.currency.source === "config"
                ? "costs_view.rate_from_config"
                : "costs_view.rate_default",
            ),
            estimated: money(totals?.estimated_usd ?? 0),
          })}
        </p>
      </div>
    </ScrollArea>
  );
}

// ---------------------------------------------------------------------------
// Which area of the app — and which kind of model inside it
// ---------------------------------------------------------------------------

/** One lucide mark per area, so the switch reads without its labels too. */
const AREA_ICONS = {
  all: Wallet,
  jarvis: Sparkles,
  "agentic-ide": Terminal,
  "jarvis-voice": AudioLines,
} as const;

/**
 * Overall · Jarvis · Jarvis Voice · Agentic IDE.
 *
 * The same pill the front page uses for Voice/Chat (`home/SurfaceSwitch`) —
 * one shape for "which area am I in", wherever the question appears.
 *
 * `value` is null when a breakdown row produced a surface selection no area
 * matches. Nothing is lit then, which is the honest state: the chips under
 * the header say what is actually filtered.
 */
function AreaSwitch({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (id: string) => void;
}) {
  const t = useT();
  return (
    <div
      role="tablist"
      aria-label={t("costs_view.area_label")}
      data-testid="costs-area-switch"
      // Hugs its labels on a real window and only goes full width when
      // there is no room to sit beside itself — a control stretched across
      // 1400px reads as a header bar, not as a choice between four things.
      className={cn(
        "flex w-full flex-wrap items-center gap-0.5 rounded-xl border border-border bg-secondary p-0.5",
        "sm:w-auto sm:flex-nowrap sm:self-start",
      )}
    >
      {SURFACE_GROUPS.map((group) => {
        const active = group.id === value;
        const Icon = AREA_ICONS[group.id as keyof typeof AREA_ICONS] ?? Wallet;
        return (
          <button
            key={group.id}
            type="button"
            role="tab"
            aria-selected={active}
            data-testid={`costs-area-${group.id}`}
            onClick={() => onChange(group.id)}
            className={cn(
              "flex flex-1 items-center justify-center gap-1.5 rounded-[10px] px-3 py-1.5 text-xs font-medium transition-colors sm:flex-none",
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
              active
                ? "bg-card text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon aria-hidden className="h-3.5 w-3.5" />
            <span className="truncate">{t(surfaceGroupLabelKey(group.id))}</span>
          </button>
        );
      })}
    </div>
  );
}

/**
 * The roles that can occur in the chosen area, as toggles.
 *
 * Only roles the data has actually seen are offered — an area that has never
 * run shows no chips rather than a row of dead buttons. A single possible
 * role is not a choice, so the row hides itself entirely.
 */
function RoleFilters({
  roles,
  available,
  active,
  onToggle,
}: {
  roles: CostRole[];
  available: CostRole[];
  active: CostRole[];
  onToggle: (role: CostRole) => void;
}) {
  const t = useT();
  const shown = roles.filter((r) => available.includes(r));
  if (shown.length < 2) return null;
  return (
    <div
      role="group"
      aria-label={t("costs_view.role_filter_label")}
      data-testid="costs-role-filters"
      className="flex flex-wrap items-center gap-1"
    >
      <span className="mr-1 hidden h-4 w-px shrink-0 bg-border sm:block" aria-hidden />
      {shown.map((role) => {
        const on = active.includes(role);
        return (
          <button
            key={role}
            type="button"
            aria-pressed={on}
            data-testid={`costs-role-${role}`}
            onClick={() => onToggle(role)}
            className={cn(
              "inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs transition-colors",
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
              on
                ? "bg-sheen/[0.08] font-medium text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <span
              className="h-2 w-2 shrink-0 rounded-[3px]"
              style={{ background: roleColor(role) }}
              aria-hidden
            />
            {t(roleLabelKey(role))}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Active filters
// ---------------------------------------------------------------------------

const CHIP_KEYS: { key: keyof CostFilters; labelKey: string }[] = [
  { key: "providers", labelKey: "costs_view.dim_provider" },
  { key: "models", labelKey: "costs_view.dim_model" },
  { key: "roles", labelKey: "costs_view.dim_role" },
  { key: "surfaces", labelKey: "costs_view.dim_surface" },
  { key: "refs", labelKey: "costs_view.dim_ref" },
];

function ActiveFilterChips({
  filters,
  onRemove,
  onClear,
  labelFor,
}: {
  filters: CostFilters;
  onRemove: (key: keyof CostFilters, value: string) => void;
  onClear: () => void;
  /** Renders an opaque id (a session) as the words the user recognises. */
  labelFor?: (value: string) => string;
}) {
  const t = useT();
  if (!hasActiveFilters(filters)) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {CHIP_KEYS.flatMap(({ key, labelKey }) =>
        (filters[key] as string[]).map((value) => (
          <button
            key={`${key}:${value}`}
            type="button"
            onClick={() => onRemove(key, value)}
            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border bg-card/60 px-2.5 text-xs text-foreground transition-colors hover:bg-sheen/[0.06]"
          >
            <span className="text-muted-foreground">{t(labelKey)}</span>
            <span className="font-medium">
              {key === "refs" && labelFor ? labelFor(value) : value}
            </span>
            <X className="h-3 w-3 text-muted-foreground" />
          </button>
        )),
      )}
      {filters.search.trim() ? (
        <span className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border bg-card/60 px-2.5 text-xs">
          <Search className="h-3 w-3 text-muted-foreground" />
          {filters.search}
        </span>
      ) : null}
      <button
        type="button"
        onClick={onClear}
        className="ml-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        {t("costs_view.clear_filters")}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Breakdown table
// ---------------------------------------------------------------------------

function BreakdownTable({
  rows,
  dimension,
  active,
  onToggle,
  money,
  loading,
}: {
  rows: CostBucket[];
  dimension: Dimension;
  active: string[];
  onToggle: (key: string) => void;
  money: (usd: number) => string;
  loading?: boolean;
}) {
  const t = useT();
  const columns: Column[] = [
    { id: "name", label: t(`costs_view.dim_${dimension}`) },
    { id: "share", label: t("costs_view.col_share"), width: "170px" },
    { id: "cost", label: t("costs_view.col_cost"), width: "110px", align: "right" },
    { id: "in", label: t("costs_view.tokens_in"), width: "92px", align: "right" },
    { id: "out", label: t("costs_view.tokens_out"), width: "92px", align: "right" },
    { id: "calls", label: t("costs_view.col_calls"), width: "80px", align: "right" },
  ];

  if (!loading && rows.length === 0) {
    return (
      <div className="px-4 pb-4">
        <EmptyRow>{t("costs_view.breakdown_empty")}</EmptyRow>
      </div>
    );
  }

  return (
    <Table label={t("costs_view.breakdown_title")}>
      <TableHead columns={columns} />
      {rows.map((row) => {
        const isActive = active.includes(row.key);
        const color = dimension === "role" ? roleColor(row.key) : keyColor(row.key);
        // A provider row IS its vendor; a model row belongs to one. Roles and
        // surfaces are ours, not a vendor's — they keep the colour chip that
        // ties the row to its stack in the chart above.
        const logoId =
          dimension === "provider"
            ? row.key
            : dimension === "model"
              ? (row.members[0] ?? row.key)
              : null;
        return (
          <TableRow
            key={row.key}
            columns={columns}
            selected={isActive}
            onClick={() => onToggle(row.key)}
            ariaLabel={row.key}
          >
            <Cell>
              <div className="flex min-w-0 items-center gap-2.5">
                {logoId ? (
                  <ProviderLogo providerId={logoId} label={row.key} size="sm" />
                ) : (
                  <span
                    className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
                    style={{ background: color }}
                    aria-hidden
                  />
                )}
                <div className="min-w-0">
                  <div className="truncate font-medium text-foreground">
                    {dimension === "role"
                      ? t(roleLabelKey(row.key))
                      : dimension === "surface"
                        ? t(surfaceLabelKey(row.key))
                        : row.key}
                  </div>
                  {row.members.length > 0 ? (
                    <div className="truncate text-xs text-muted-foreground">
                      {row.members
                        .map((m) =>
                          dimension === "surface" || dimension === "provider"
                            ? m in ROLE_KEYS
                              ? t(roleLabelKey(m))
                              : m
                            : m,
                        )
                        .join(" · ")}
                    </div>
                  ) : null}
                </div>
              </div>
            </Cell>
            <Cell>
              <div className="flex items-center gap-2">
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-sheen/[0.08]">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.max(2, Math.round(row.cost_share * 100))}%`,
                      background: color,
                    }}
                  />
                </div>
                <span className="w-10 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                  {formatShare(row.cost_share)}
                </span>
              </div>
            </Cell>
            <Cell align="right">
              <span className="font-medium tabular-nums text-foreground">{money(row.cost_usd)}</span>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums" title={formatExact(row.tokens_in)}>
                {formatTokensOrNone(row.tokens_in)}
              </span>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums" title={formatExact(row.tokens_out)}>
                {formatTokensOrNone(row.tokens_out)}
              </span>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums">{formatExact(row.entries)}</span>
            </Cell>
          </TableRow>
        );
      })}
    </Table>
  );
}

/** Role ids as an object, so a member name can be checked without a cast. */
const ROLE_KEYS: Record<string, true> = {
  realtime: true,
  tool: true,
  pipeline: true,
  agent: true,
  worker: true,
};

// ---------------------------------------------------------------------------
// Speech models — what each ear and mouth consumed, at API list price
// ---------------------------------------------------------------------------

/** Audio duration in the unit a person would say it in. */
function formatAudio(ms: number): string {
  if (ms <= 0) return "—";
  const s = ms / 1000;
  if (s < 90) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  const min = s / 60;
  if (min < 90) return `${min.toFixed(1)} min`;
  return `${(min / 60).toFixed(1)} h`;
}

/**
 * One row per speech model in the area: how much it heard or spoke, how many
 * turns that was, and what the same work costs at the vendor's on-demand API
 * rate — total and per call. Only the speech area shows this: a brain model
 * has tokens and the breakdown above already says everything about it, while
 * a speech model has none, so without this table its row reads "0 tokens" and
 * the number that matters (characters, seconds) is nowhere.
 */
function SpeechModelsTable({
  rows,
  money,
  loading,
}: {
  rows: CostBucket[];
  money: (usd: number) => string;
  loading?: boolean;
}) {
  const t = useT();
  const columns: Column[] = [
    { id: "model", label: t("costs_view.dim_model") },
    { id: "calls", label: t("costs_view.col_calls"), width: "84px", align: "right" },
    { id: "heard", label: t("costs_view.col_heard"), width: "100px", align: "right" },
    { id: "spoken", label: t("costs_view.col_spoken"), width: "110px", align: "right" },
    { id: "cost", label: t("costs_view.col_api_cost"), width: "110px", align: "right" },
    { id: "per_call", label: t("costs_view.col_per_call"), width: "110px", align: "right" },
    { id: "rate", label: t("costs_view.col_rate"), width: "110px", align: "right" },
  ];

  if (!loading && rows.length === 0) {
    return (
      <div className="px-4 pb-4">
        <EmptyRow>{t("costs_view.speech_models_empty")}</EmptyRow>
      </div>
    );
  }

  return (
    <Table label={t("costs_view.speech_models_title")}>
      <TableHead columns={columns} />
      {rows.map((row) => {
        const provider = row.members[0] ?? "";
        const perCall = row.entries > 0 ? row.cost_usd / row.entries : 0;
        return (
          <TableRow key={row.key} columns={columns} ariaLabel={row.key}>
            <Cell>
              <div className="flex min-w-0 items-center gap-2">
                {provider ? <ProviderLogo providerId={provider} label={provider} size="sm" /> : null}
                <div className="min-w-0">
                  <div className="truncate font-medium text-foreground">{row.key}</div>
                  {provider ? (
                    <div className="truncate text-xs text-muted-foreground">{provider}</div>
                  ) : null}
                </div>
              </div>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums">{formatExact(row.entries)}</span>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums">{formatAudio(row.audio_ms)}</span>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums" title={formatExact(row.chars)}>
                {row.chars > 0 ? formatTokensOrNone(row.chars) : "—"}
              </span>
            </Cell>
            <Cell align="right">
              <span className="font-medium tabular-nums text-foreground">{money(row.cost_usd)}</span>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums">{money(perCall)}</span>
            </Cell>
            <Cell align="right" muted>
              {/* "$0.00" means two different things; the worst case is named. */}
              <span>
                {t(
                  priceSourceLabelKey(
                    row.price_sources.includes("unknown")
                      ? "unknown"
                      : (row.price_sources[0] ?? "recorded"),
                  ),
                )}
              </span>
            </Cell>
          </TableRow>
        );
      })}
    </Table>
  );
}

// ---------------------------------------------------------------------------
// Where it went — the most expensive conversations, missions and agent runs
// ---------------------------------------------------------------------------

interface RefRowShape extends CostBucket {
  label: string;
  surface: string;
}

/**
 * The breakdown answers "which model"; this one answers "which conversation".
 *
 * A session id means nothing to a person, so each row leads with what was
 * actually said (or asked of a mission) and keeps the id only as the filter
 * value behind the click.
 */
function TopRefsTable({
  rows,
  active,
  onToggle,
  money,
  loading,
}: {
  rows: RefRowShape[];
  active: string[];
  onToggle: (key: string) => void;
  money: (usd: number) => string;
  loading?: boolean;
}) {
  const t = useT();
  const columns: Column[] = [
    { id: "what", label: t("costs_view.col_session") },
    { id: "share", label: t("costs_view.col_share"), width: "150px" },
    { id: "cost", label: t("costs_view.col_cost"), width: "110px", align: "right" },
    { id: "tokens", label: t("costs_view.stat_tokens"), width: "96px", align: "right" },
    { id: "when", label: t("costs_view.col_when"), width: "132px", align: "right" },
  ];

  if (!loading && rows.length === 0) {
    return (
      <div className="px-4 pb-4">
        <EmptyRow>{t("costs_view.top_refs_empty")}</EmptyRow>
      </div>
    );
  }

  return (
    <Table label={t("costs_view.top_refs_title")}>
      <TableHead columns={columns} />
      {rows.map((row) => {
        const isActive = active.includes(row.key);
        return (
          <TableRow
            key={row.key}
            columns={columns}
            selected={isActive}
            onClick={() => onToggle(row.key)}
            ariaLabel={row.label || row.key}
          >
            <Cell>
              <div className="min-w-0">
                <div className="truncate font-medium text-foreground">
                  {row.label || `${row.key.slice(0, 8)}…`}
                </div>
                <div className="truncate text-xs text-muted-foreground">
                  {t(surfaceLabelKey(row.surface))}
                  {row.members.length > 0 ? ` · ${row.members.join(" · ")}` : ""}
                </div>
              </div>
            </Cell>
            <Cell>
              <div className="flex items-center gap-2">
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-sheen/[0.08]">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.max(2, Math.round(row.cost_share * 100))}%`,
                      background: keyColor(row.surface),
                    }}
                  />
                </div>
                <span className="w-10 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                  {formatShare(row.cost_share)}
                </span>
              </div>
            </Cell>
            <Cell align="right">
              <span className="font-medium tabular-nums text-foreground">{money(row.cost_usd)}</span>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums" title={formatExact(row.tokens_total)}>
                {formatTokensOrNone(row.tokens_total)}
              </span>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums">{formatTimestamp(row.last_ts_ms)}</span>
            </Cell>
          </TableRow>
        );
      })}
    </Table>
  );
}

// ---------------------------------------------------------------------------
// Line items
// ---------------------------------------------------------------------------

interface EntryRowShape {
  ts_ms: number;
  surface: CostSurface;
  role: CostRole;
  provider: string;
  model: string;
  tokens_in: number;
  tokens_out: number;
  tokens_cached: number;
  tokens_total: number;
  cost_usd: number;
  price_source: string;
  ref_id: string;
  label: string;
}

function EntriesTable({
  rows,
  money,
  loading,
  onFilterModel,
}: {
  rows: EntryRowShape[];
  money: (usd: number) => string;
  loading?: boolean;
  /** Omitted inside a day report - there is nothing above it to filter. */
  onFilterModel?: (model: string) => void;
}) {
  const t = useT();
  const columns: Column[] = [
    { id: "when", label: t("costs_view.col_when"), width: "132px" },
    { id: "what", label: t("costs_view.col_what") },
    { id: "price", label: t("costs_view.col_price_source"), width: "128px" },
    { id: "tokens", label: t("costs_view.col_tokens"), width: "128px", align: "right" },
    { id: "cost", label: t("costs_view.col_cost"), width: "104px", align: "right" },
  ];

  if (!loading && rows.length === 0) {
    return (
      <div className="px-4 pb-4">
        <EmptyRow>{t("costs_view.entries_empty")}</EmptyRow>
      </div>
    );
  }

  return (
    <Table label={t("costs_view.entries_title")}>
      <TableHead columns={columns} />
      {rows.map((row, i) => (
        <TableRow
          key={`${row.ts_ms}-${row.ref_id}-${i}`}
          columns={columns}
          onClick={row.model && onFilterModel ? () => onFilterModel(row.model) : undefined}
          ariaLabel={row.model || row.provider}
        >
          <Cell muted>
            <span className="tabular-nums">{formatTimestamp(row.ts_ms)}</span>
          </Cell>
          <Cell>
            <div className="flex min-w-0 items-center gap-2.5">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
                style={{ background: roleColor(row.role) }}
                aria-hidden
              />
              <div className="min-w-0">
                <div className="truncate font-medium text-foreground">
                  {row.model || row.provider}
                </div>
                <div className="truncate text-xs text-muted-foreground">
                  {t(roleLabelKey(row.role))} · {t(surfaceLabelKey(row.surface))}
                  {row.label ? ` · ${row.label}` : ""}
                </div>
              </div>
            </div>
          </Cell>
          <Cell>
            <StatusDot
              tone={priceSourceTone(row.price_source as never)}
              label={t(priceSourceLabelKey(row.price_source))}
            />
          </Cell>
          <Cell align="right" muted>
            <span
              className="tabular-nums"
              title={`${formatExact(row.tokens_in)} in / ${formatExact(row.tokens_out)} out`}
            >
              {formatTokensOrNone(row.tokens_in)} / {formatTokensOrNone(row.tokens_out)}
            </span>
          </Cell>
          <Cell align="right">
            <span className="font-medium tabular-nums text-foreground">{money(row.cost_usd)}</span>
          </Cell>
        </TableRow>
      ))}
    </Table>
  );
}

// ---------------------------------------------------------------------------
// Rate card
// ---------------------------------------------------------------------------

interface RateRowShape {
  model: string;
  input_usd_per_mtok: number | null;
  output_usd_per_mtok: number | null;
  audio_input_usd_per_mtok: number | null;
  audio_output_usd_per_mtok: number | null;
  known: boolean;
}

function RatesTable({
  rates,
  currency,
  eurPerUsd,
  loading,
}: {
  rates: RateRowShape[];
  currency: Currency;
  eurPerUsd: number;
  loading?: boolean;
}) {
  const t = useT();
  const columns: Column[] = [
    { id: "model", label: t("costs_view.dim_model") },
    { id: "in", label: t("costs_view.rate_in"), width: "116px", align: "right" },
    { id: "out", label: t("costs_view.rate_out"), width: "116px", align: "right" },
    { id: "audio", label: t("costs_view.rate_audio"), width: "150px", align: "right" },
  ];
  const price = (v: number | null) =>
    v === null ? "—" : formatMoney(v, eurPerUsd, currency);

  if (!loading && rates.length === 0) {
    return (
      <div className="px-4 pb-4">
        <EmptyRow>{t("costs_view.rates_empty")}</EmptyRow>
      </div>
    );
  }

  return (
    <div className="border-t border-border">
      <Table label={t("costs_view.rates_title")}>
        <TableHead columns={columns} />
        {rates.map((rate) => (
          <TableRow key={rate.model} columns={columns}>
            <Cell>
              <div className="flex min-w-0 items-center gap-2">
                <Hash className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate text-foreground">{rate.model}</span>
                {!rate.known ? (
                  <span className="shrink-0 rounded border border-amber-500/40 px-1.5 py-0.5 text-[10px] text-amber-500">
                    {t("costs_view.rate_unknown")}
                  </span>
                ) : null}
              </div>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums">{price(rate.input_usd_per_mtok)}</span>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums">{price(rate.output_usd_per_mtok)}</span>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums">
                {rate.audio_input_usd_per_mtok === null
                  ? "—"
                  : `${price(rate.audio_input_usd_per_mtok)} / ${price(rate.audio_output_usd_per_mtok)}`}
              </span>
            </Cell>
          </TableRow>
        ))}
      </Table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The daily ledger — one row per day, and the report behind it
// ---------------------------------------------------------------------------

/**
 * One row per calendar day.
 *
 * This replaced a flat list of every session: the section used to put five to
 * fifteen rows on a single day, each one a session labelled with the model of
 * that session, and the day's real composition was impossible to see. Worse,
 * a session row carries the timestamp of its FIRST call, so "newest first"
 * buried a long morning run under a short one started later — a day of one
 * model's work read as a day of something else entirely (maintainer,
 * 2026-08-25).
 *
 * A day has no such ambiguity. It names every model it saw, biggest first,
 * whichever vendor they came from, and opens into the full report.
 */
function DailyTable({
  rows,
  money,
  loading,
  onOpen,
}: {
  rows: CostDayRow[];
  money: (usd: number) => string;
  loading?: boolean;
  onOpen: (date: string) => void;
}) {
  const t = useT();
  const columns: Column[] = [
    { id: "day", label: t("costs_view.col_day"), width: "196px" },
    { id: "models", label: t("costs_view.col_models") },
    { id: "calls", label: t("costs_view.col_calls"), width: "80px", align: "right" },
    { id: "tokens", label: t("costs_view.stat_tokens"), width: "116px", align: "right" },
    { id: "cost", label: t("costs_view.col_cost"), width: "132px", align: "right" },
    { id: "open", label: "", width: "32px", align: "right" },
  ];

  if (!loading && rows.length === 0) {
    return (
      <div className="px-4 pb-4">
        <EmptyRow>{t("costs_view.daily_empty")}</EmptyRow>
      </div>
    );
  }

  return (
    <Table label={t("costs_view.daily_title")}>
      <TableHead columns={columns} />
      {rows.map((row) => {
        const rel = relativeDay(row.date);
        const seat = row.totals.subscription_usd;
        const billed = Math.max(0, row.totals.cost_usd - seat);
        // A day where nothing was billed still ranks its models — by tokens,
        // because a share of zero dollars tells nobody anything.
        const priced = row.totals.cost_usd > 0;
        const shown = row.by_model.slice(0, 3);
        const rest = row.by_model.length - shown.length;
        return (
          <TableRow
            key={row.date}
            columns={columns}
            onClick={() => onOpen(row.date)}
            ariaLabel={fill(t("costs_view.day_open"), { day: row.date })}
          >
            <Cell>
              <div className="flex min-w-0 items-center gap-2.5">
                <CalendarDays className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                <div className="min-w-0">
                  <div className="truncate font-medium text-foreground">
                    {rel ? t(`costs_view.daily_${rel}`) : formatDayShort(row.date)}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    {rel
                      ? formatDayShort(row.date)
                      : fill(t("costs_view.day_byline_window"), {
                          from: formatClock(row.totals.first_ts_ms),
                          to: formatClock(row.totals.last_ts_ms),
                        })}
                  </div>
                </div>
              </div>
            </Cell>
            <Cell>
              <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                {shown.map((m) => (
                  <span
                    key={m.key}
                    className="inline-flex max-w-[210px] items-center gap-1.5 rounded-md border border-border bg-card/60 px-1.5 py-0.5 text-xs"
                    title={m.key}
                  >
                    <span
                      className="h-2 w-2 shrink-0 rounded-[2px]"
                      style={{ background: keyColor(m.key) }}
                      aria-hidden
                    />
                    <span className="truncate text-foreground/90">{m.key}</span>
                    <span className="shrink-0 tabular-nums text-muted-foreground">
                      {formatShare(priced ? m.cost_share : m.token_share)}
                    </span>
                  </span>
                ))}
                {rest > 0 ? (
                  <span className="text-xs text-muted-foreground">
                    {fill(t("costs_view.daily_more_models"), { count: String(rest) })}
                  </span>
                ) : null}
              </div>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums">{formatExact(row.totals.entries)}</span>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums" title={formatExact(row.totals.tokens_total)}>
                {formatTokensOrNone(row.totals.tokens_total)}
              </span>
            </Cell>
            <Cell align="right">
              <div>
                <div className="font-medium tabular-nums text-foreground">
                  {money(row.totals.cost_usd)}
                </div>
                {seat > 0 ? (
                  <div
                    className="text-[11px] tabular-nums text-muted-foreground"
                    title={t("costs_view.day_stat_billed")}
                  >
                    {money(billed)}
                  </div>
                ) : null}
              </div>
            </Cell>
            <Cell align="right" muted>
              <ChevronRight className="h-4 w-4" aria-hidden />
            </Cell>
          </TableRow>
        );
      })}
    </Table>
  );
}

/**
 * One day, in full — the answer to "where did today's money and tokens go".
 *
 * Everything on this page is the SAME request the section above makes, only
 * bounded to one local day (`since_ms`/`until_ms`), so a day can never
 * disagree with the range it belongs to. Nothing is vendor-specific: whatever
 * models, providers and areas the day actually saw are what it names.
 */
function DayReport({
  date,
  filters,
  currency,
  eurPerUsd,
  onBack,
}: {
  date: string;
  filters: CostFilters;
  currency: Currency;
  eurPerUsd: number;
  onBack: () => void;
}) {
  const t = useT();
  const [metric, setMetric] = useState<TrendMetric>("cost");
  const [sort, setSort] = useState<EntrySort>("cost");
  const [visible, setVisible] = useState(ENTRY_PAGE);

  const [sinceMs, untilMs] = dayBoundsMs(date);
  // The row filters carry over — drilling into a day from a filtered section
  // must not silently widen back out to everything.
  const dayFilters = useMemo(
    () => ({ ...filters, sinceMs, untilMs }),
    [filters, sinceMs, untilMs],
  );
  const summary = useCostSummary(dayFilters);
  const entries = useCostEntries(dayFilters, sort, visible, 0);

  const data = summary.data;
  const totals = data?.totals;
  const money = (usd: number) => formatMoney(usd, eurPerUsd, currency);
  const seat = totals?.subscription_usd ?? 0;
  const billed = Math.max(0, (totals?.cost_usd ?? 0) - seat);
  const priced = (totals?.cost_usd ?? 0) > 0;
  const rel = relativeDay(date);
  const long = formatDayLong(date);

  return (
    <ScrollArea className="h-full">
      <div className="mx-auto flex max-w-[1180px] flex-col gap-4 px-6 py-6">
        <BackLink label={t("costs_view.day_back")} onClick={onBack} />

        <DetailHeader
          leading={
            <div className="rounded-xl border border-border bg-card/50 p-2.5">
              <CalendarDays className="h-5 w-5 text-muted-foreground" aria-hidden />
            </div>
          }
          title={rel ? t(`costs_view.daily_${rel}`) : long}
          titleAccessory={
            rel ? <span className="text-sm text-muted-foreground">{long}</span> : null
          }
          byline={
            data
              ? `${fill(t("costs_view.day_byline"), {
                  calls: formatExact(totals?.entries ?? 0),
                  models: String(data.by_model.length),
                  providers: String(data.by_provider.length),
                })} · ${fill(t("costs_view.day_byline_window"), {
                  from: formatClock(totals?.first_ts_ms ?? 0),
                  to: formatClock(totals?.last_ts_ms ?? 0),
                })}`
              : t("costs_view.subtitle_loading")
          }
          actions={
            <IconButton
              label={t("costs_view.refresh")}
              busy={summary.isFetching}
              onClick={() => void summary.refetch()}
            >
              <RefreshCw className="h-4 w-4" />
            </IconButton>
          }
        />

        {summary.isError ? <EmptyRow>{t("costs_view.load_error")}</EmptyRow> : null}

        {/* ---------------------------------------------------------------
            Three money numbers, deliberately kept apart: what the day was
            WORTH at list price, what an API key was actually charged, and
            what a monthly seat already covered. Folding the third into the
            first is how a dashboard starts reading like a bill nobody got.
        --------------------------------------------------------------- */}
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <StatTile
            icon={<Coins className="h-4 w-4" />}
            label={t("costs_view.day_stat_worth")}
            value={money(totals?.cost_usd ?? 0)}
            hint={t("costs_view.day_stat_worth_hint")}
            loading={summary.isLoading}
          />
          <StatTile
            icon={<CreditCard className="h-4 w-4" />}
            label={t("costs_view.day_stat_billed")}
            value={money(billed)}
            hint={t("costs_view.day_stat_billed_hint")}
            tone={billed > 0 ? "primary" : "ok"}
            loading={summary.isLoading}
          />
          <StatTile
            icon={<Ticket className="h-4 w-4" />}
            label={t("costs_view.day_stat_seat")}
            value={seat > 0 ? money(seat) : "—"}
            hint={seat > 0 ? t("costs_view.day_stat_seat_hint") : t("costs_view.day_stat_seat_none")}
            loading={summary.isLoading}
          />
          <StatTile
            icon={<Sigma className="h-4 w-4" />}
            label={t("costs_view.stat_tokens")}
            value={formatTokens(totals?.tokens_total ?? 0)}
            hint={fill(
              t(
                (totals?.tokens_cached ?? 0) > 0
                  ? "costs_view.stat_tokens_hint_cached"
                  : "costs_view.stat_tokens_hint",
              ),
              {
                in: formatTokens(totals?.tokens_in ?? 0),
                cached: formatTokens(totals?.tokens_cached ?? 0),
                out: formatTokens(totals?.tokens_out ?? 0),
              },
            )}
            loading={summary.isLoading}
          />
        </div>

        {/* Hour by hour ------------------------------------------------- */}
        <Panel className="p-4">
          <PanelHeader
            title={t("costs_view.day_hours_title")}
            subtitle={t("costs_view.day_hours_subtitle")}
            actions={
              <SegmentedFilter<TrendMetric>
                label={t("costs_view.metric_label")}
                value={metric}
                onChange={setMetric}
                options={[
                  { id: "cost", label: t("costs_view.metric_cost") },
                  { id: "tokens", label: t("costs_view.metric_tokens") },
                ]}
              />
            }
          />
          <div className="mt-3 h-[220px]">
            <CostTrendChart
              series={data?.series ?? []}
              bucket="hour"
              metric={metric}
              currency={currency}
              eurPerUsd={eurPerUsd}
            />
          </div>
        </Panel>

        {/* Which models ------------------------------------------------ */}
        <Panel>
          <div className="px-4 pt-4">
            <PanelHeader
              title={t("costs_view.day_models_title")}
              subtitle={t("costs_view.day_models_subtitle")}
            />
          </div>
          <div className="mt-3">
            <DayModelsTable
              rows={data?.by_model ?? []}
              priced={priced}
              money={money}
              loading={summary.isLoading}
            />
          </div>
        </Panel>

        {/* Token composition ------------------------------------------- */}
        <Panel className="p-4">
          <PanelHeader
            title={t("costs_view.day_tokens_title")}
            subtitle={t("costs_view.day_tokens_subtitle")}
          />
          <TokenSplitBar
            tokensIn={totals?.tokens_in ?? 0}
            tokensCached={totals?.tokens_cached ?? 0}
            tokensOut={totals?.tokens_out ?? 0}
          />
        </Panel>

        {/* Where in the app -------------------------------------------- */}
        <Panel className="p-4">
          <PanelHeader
            title={t("costs_view.day_split_title")}
            subtitle={t("costs_view.day_split_subtitle")}
          />
          <div className="mt-4 grid gap-6 md:grid-cols-2">
            <ShareBars
              rows={data?.by_surface ?? []}
              labelOf={(key) => t(surfaceLabelKey(key))}
              colorOf={(key) => keyColor(key)}
              priced={priced}
              money={money}
              empty={t("costs_view.breakdown_empty")}
            />
            <ShareBars
              rows={data?.by_role ?? []}
              labelOf={(key) => t(roleLabelKey(key))}
              colorOf={(key) => roleColor(key)}
              priced={priced}
              money={money}
              empty={t("costs_view.breakdown_empty")}
            />
          </div>
        </Panel>

        {/* Which sessions ---------------------------------------------- */}
        <Panel>
          <div className="px-4 pt-4">
            <PanelHeader
              title={t("costs_view.top_refs_title")}
              subtitle={t("costs_view.top_refs_subtitle")}
            />
          </div>
          <div className="mt-3">
            <TopRefsTable
              rows={data?.top_refs ?? []}
              active={[]}
              onToggle={() => undefined}
              money={money}
              loading={summary.isLoading}
            />
          </div>
        </Panel>

        {/* Every call --------------------------------------------------- */}
        <Panel>
          <div className="px-4 pt-4">
            <PanelHeader
              title={t("costs_view.day_calls_title")}
              subtitle={fill(t("costs_view.entries_subtitle"), {
                shown: formatExact(Math.min(entries.data?.total ?? 0, visible)),
                total: formatExact(entries.data?.total ?? 0),
              })}
              actions={
                <SegmentedFilter<EntrySort>
                  label={t("costs_view.sort_label")}
                  value={sort}
                  onChange={(v) => {
                    setSort(v);
                    setVisible(ENTRY_PAGE);
                  }}
                  options={[
                    { id: "cost", label: t("costs_view.sort_cost") },
                    { id: "tokens", label: t("costs_view.sort_tokens") },
                    { id: "recent", label: t("costs_view.sort_recent") },
                  ]}
                />
              }
            />
          </div>
          <div className="mt-3">
            <EntriesTable
              rows={entries.data?.items ?? []}
              money={money}
              loading={entries.isLoading}
            />
          </div>
          {(entries.data?.total ?? 0) > visible ? (
            <div className="border-t border-border px-4 py-3">
              <button
                type="button"
                onClick={() => setVisible((v) => v + ENTRY_PAGE)}
                className="text-sm font-medium text-foreground/80 transition-colors hover:text-foreground"
              >
                {fill(t("costs_view.load_more"), { count: String(ENTRY_PAGE) })}
              </button>
            </div>
          ) : null}
        </Panel>
      </div>
    </ScrollArea>
  );
}

/** Every model of one day: how much of it, how many tokens, what it cost. */
function DayModelsTable({
  rows,
  priced,
  money,
  loading,
}: {
  rows: CostBucket[];
  /** Did the day cost anything? If not, the bar ranks by tokens instead. */
  priced: boolean;
  money: (usd: number) => string;
  loading?: boolean;
}) {
  const t = useT();
  const columns: Column[] = [
    { id: "model", label: t("costs_view.dim_model") },
    { id: "share", label: t("costs_view.col_share"), width: "150px" },
    { id: "calls", label: t("costs_view.col_calls"), width: "76px", align: "right" },
    { id: "in", label: t("costs_view.tokens_in"), width: "92px", align: "right" },
    { id: "cached", label: t("costs_view.tokens_cached"), width: "92px", align: "right" },
    { id: "out", label: t("costs_view.tokens_out"), width: "92px", align: "right" },
    { id: "cost", label: t("costs_view.col_cost"), width: "112px", align: "right" },
  ];

  if (!loading && rows.length === 0) {
    return (
      <div className="px-4 pb-4">
        <EmptyRow>{t("costs_view.breakdown_empty")}</EmptyRow>
      </div>
    );
  }

  return (
    <Table label={t("costs_view.day_models_title")}>
      <TableHead columns={columns} />
      {rows.map((row) => {
        const share = priced ? row.cost_share : row.token_share;
        const provider = row.members[0] ?? "";
        return (
          <TableRow key={row.key} columns={columns} ariaLabel={row.key}>
            <Cell>
              <div className="flex min-w-0 items-center gap-2.5">
                {provider ? (
                  <ProviderLogo providerId={provider} label={row.key} size="sm" />
                ) : (
                  <span
                    className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
                    style={{ background: keyColor(row.key) }}
                    aria-hidden
                  />
                )}
                <div className="min-w-0">
                  <div className="truncate font-medium text-foreground">{row.key}</div>
                  {provider ? (
                    <div className="truncate text-xs text-muted-foreground">{provider}</div>
                  ) : null}
                </div>
              </div>
            </Cell>
            <Cell>
              <div className="flex items-center gap-2">
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-sheen/[0.08]">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.max(2, Math.round(share * 100))}%`,
                      background: keyColor(row.key),
                    }}
                  />
                </div>
                <span className="w-10 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                  {formatShare(share)}
                </span>
              </div>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums">{formatExact(row.entries)}</span>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums" title={formatExact(row.tokens_in)}>
                {formatTokensOrNone(row.tokens_in)}
              </span>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums" title={formatExact(row.tokens_cached)}>
                {formatTokensOrNone(row.tokens_cached)}
              </span>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums" title={formatExact(row.tokens_out)}>
                {formatTokensOrNone(row.tokens_out)}
              </span>
            </Cell>
            <Cell align="right">
              <span className="font-medium tabular-nums text-foreground">
                {money(row.cost_usd)}
              </span>
            </Cell>
          </TableRow>
        );
      })}
    </Table>
  );
}

/**
 * The day's tokens as one bar: fresh input, cache reads, output.
 *
 * The three bill at three different rates, and the cheapest of them is
 * usually the largest — a single "tokens" number hides exactly the thing
 * that explains the bill.
 */
function TokenSplitBar({
  tokensIn,
  tokensCached,
  tokensOut,
}: {
  tokensIn: number;
  tokensCached: number;
  tokensOut: number;
}) {
  const t = useT();
  const parts = [
    { id: "tokens_in", value: tokensIn, color: "hsl(199 90% 62%)" },
    { id: "tokens_cached", value: tokensCached, color: "hsl(268 72% 68%)" },
    { id: "tokens_out", value: tokensOut, color: "hsl(50 100% 52%)" },
  ];
  const total = parts.reduce((sum, p) => sum + p.value, 0);

  if (total <= 0) {
    return (
      <div className="mt-4">
        <EmptyRow>{t("costs_view.chart_empty")}</EmptyRow>
      </div>
    );
  }

  return (
    <div className="mt-4">
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-sheen/[0.08]">
        {parts
          .filter((p) => p.value > 0)
          .map((p) => (
            <div
              key={p.id}
              style={{ width: `${(p.value / total) * 100}%`, background: p.color }}
              title={`${t(`costs_view.${p.id}`)} · ${formatExact(p.value)}`}
            />
          ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2">
        {parts.map((p) => (
          <div key={p.id} className="flex items-center gap-2">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
              style={{ background: p.color }}
              aria-hidden
            />
            <span className="text-sm text-muted-foreground">{t(`costs_view.${p.id}`)}</span>
            <span
              className="text-sm font-medium tabular-nums text-foreground"
              title={formatExact(p.value)}
            >
              {formatTokensOrNone(p.value)}
            </span>
            <span className="text-xs tabular-nums text-muted-foreground">
              {formatShare(p.value / total)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** A small ranked bar list — the day cut by area, or by what a model did. */
function ShareBars({
  rows,
  labelOf,
  colorOf,
  priced,
  money,
  empty,
}: {
  rows: CostBucket[];
  labelOf: (key: string) => string;
  colorOf: (key: string) => string;
  priced: boolean;
  money: (usd: number) => string;
  empty: string;
}) {
  if (rows.length === 0) {
    return <EmptyRow>{empty}</EmptyRow>;
  }
  return (
    <div className="flex flex-col gap-3">
      {rows.map((row) => {
        const share = priced ? row.cost_share : row.token_share;
        return (
          <div key={row.key}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="truncate text-foreground">{labelOf(row.key)}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {money(row.cost_usd)} · {formatShare(share)}
              </span>
            </div>
            <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-sheen/[0.08]">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${Math.max(2, Math.round(share * 100))}%`,
                  background: colorOf(row.key),
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
