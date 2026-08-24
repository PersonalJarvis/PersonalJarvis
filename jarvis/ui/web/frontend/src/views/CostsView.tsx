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
  ChevronDown,
  Coins,
  Hash,
  Layers,
  RefreshCw,
  Search,
  Sigma,
  Tag,
  X,
} from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Cell,
  Column,
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
  formatBucketFull,
  formatExact,
  formatMoney,
  formatShare,
  formatTimestamp,
  formatTokens,
  keyColor,
  priceSourceLabelKey,
  priceSourceTone,
  roleColor,
  roleLabelKey,
  surfaceLabelKey,
} from "@/components/costs/costFormat";
import {
  EMPTY_FILTERS,
  hasActiveFilters,
  useCostEntries,
  useCostPricing,
  useCostSummary,
  type CostBucket,
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
  const [sort, setSort] = useState<EntrySort>("recent");
  const [visible, setVisible] = useState(ENTRY_PAGE);
  const [searchOpen, setSearchOpen] = useState(false);
  const [ratesOpen, setRatesOpen] = useState(false);

  const summary = useCostSummary(filters);
  const entries = useCostEntries(filters, sort, visible, 0);
  const pricing = useCostPricing(filters.days, ratesOpen);

  const data = summary.data;
  const eurPerUsd = data?.currency.eur_per_usd ?? 0.92;
  const totals = data?.totals;
  const money = (usd: number) => formatMoney(usd, eurPerUsd, currency);

  // One toggle for every filter dimension: clicking a row that is already
  // filtered removes it again, so a drill-down is never a one-way door.
  const toggle = (key: keyof CostFilters, value: string) => {
    setVisible(ENTRY_PAGE);
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

  const topSpender = data?.by_provider[0];
  const rangeMs = Math.max(1, (data?.until_ms ?? 0) - (data?.since_ms ?? 0));
  const perDay = totals ? totals.cost_usd / Math.max(1, rangeMs / 86_400_000) : 0;

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
                onChange={(v) => {
                  setVisible(ENTRY_PAGE);
                  setFilters((f) => ({ ...f, days: Number(v) }));
                }}
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

        {searchOpen ? (
          <div className="max-w-sm">
            <InlineSearch
              autoFocus
              value={filters.search}
              onChange={(v) => {
                setVisible(ENTRY_PAGE);
                setFilters((f) => ({ ...f, search: v }));
              }}
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
          onClear={() => {
            setVisible(ENTRY_PAGE);
            setFilters((f) => ({ ...EMPTY_FILTERS, days: f.days }));
          }}
        />

        {summary.isError ? (
          <EmptyRow>{t("costs_view.load_error")}</EmptyRow>
        ) : null}

        {/* ---------------------------------------------------------------
            Headline numbers. Cost and tokens sit side by side on purpose:
            the cheapest provider is often the loudest token consumer, and
            one number without the other hides that.
        --------------------------------------------------------------- */}
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <StatTile
            icon={<Coins className="h-4 w-4" />}
            label={t("costs_view.stat_total")}
            value={money(totals?.cost_usd ?? 0)}
            hint={fill(t("costs_view.stat_total_hint"), { perDay: money(perDay) })}
            loading={summary.isLoading}
          />
          <StatTile
            icon={<Sigma className="h-4 w-4" />}
            label={t("costs_view.stat_tokens")}
            value={formatTokens(totals?.tokens_total ?? 0)}
            hint={fill(t("costs_view.stat_tokens_hint"), {
              in: formatTokens(totals?.tokens_in ?? 0),
              out: formatTokens(totals?.tokens_out ?? 0),
            })}
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
            value={formatTokens(totals?.gap_tokens ?? 0)}
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

        {/* Line items -------------------------------------------------- */}
        <Panel>
          <div className="px-4 pt-4">
            <PanelHeader
              title={t("costs_view.entries_title")}
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
                    { id: "recent", label: t("costs_view.sort_recent") },
                    { id: "cost", label: t("costs_view.sort_cost") },
                    { id: "tokens", label: t("costs_view.sort_tokens") },
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
              onFilterModel={(model) => toggle("models", model)}
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
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
                  style={{ background: color }}
                  aria-hidden
                />
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
                {formatTokens(row.tokens_in)}
              </span>
            </Cell>
            <Cell align="right" muted>
              <span className="tabular-nums" title={formatExact(row.tokens_out)}>
                {formatTokens(row.tokens_out)}
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
                {formatTokens(row.tokens_total)}
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
  onFilterModel: (model: string) => void;
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
          onClick={row.model ? () => onFilterModel(row.model) : undefined}
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
              {formatTokens(row.tokens_in)} / {formatTokens(row.tokens_out)}
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
