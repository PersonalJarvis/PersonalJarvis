import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Panel, PanelHeader, SegmentedFilter } from "@/components/extensions/primitives";
import { costSummaryQueryOptions, type CostFilters, type CostSummary } from "@/hooks/useCosts";
import { useT } from "@/i18n";
import { CostTrendChart, type TrendMetric } from "./CostTrendChart";

/** Chart-only filtering leaves the surrounding ledger and its totals intact. */
export function CostTrendPanel({
  filters,
  summary,
  loading,
  title,
  subtitle,
  currency,
  eurPerUsd,
  bucket = "day",
}: {
  filters: CostFilters;
  summary?: CostSummary;
  loading: boolean;
  title: string;
  subtitle: string;
  currency: "usd" | "eur";
  eurPerUsd: number;
  bucket?: "day" | "hour";
}) {
  const t = useT();
  const [metric, setMetric] = useState<TrendMetric>("cost");
  const [includeIde, setIncludeIde] = useState(true);
  // Roles overlap: both agent chats and coding sessions can use "agent".
  // Filter the actual surfaces reported by the backend, including new ones.
  const surfaces = (filters.surfaces.length ? filters.surfaces : summary?.facets.surfaces ?? [])
    .filter((surface) => surface !== "agentic-ide");
  const chartQuery = useQuery({
    ...costSummaryQueryOptions({ ...filters, surfaces }),
    enabled: !includeIde && !!summary && surfaces.length > 0,
    refetchInterval: 120_000,
    // No placeholder from the previous filter: it would briefly claim the
    // dominant coding spend was already excluded while still plotting it.
  });
  // An empty surface list means "all" to the API, so an IDE-only selection
  // must render empty locally instead of accidentally requesting everything.
  const chartData = includeIde ? summary : surfaces.length ? chartQuery.data : undefined;
  const chartLoading = includeIde
    ? loading
    : loading || (surfaces.length > 0 && chartQuery.isPending);
  const chartError = !includeIde && surfaces.length > 0 && chartQuery.isError;

  return (
    <Panel className="p-4">
      <PanelHeader
        className="flex-wrap [&>div]:max-w-full"
        title={title}
        subtitle={subtitle}
        actions={
          <div className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-2">
            <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={includeIde}
                onChange={(event) => setIncludeIde(event.target.checked)}
                className="h-4 w-4 accent-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              {t("costs_view.chart_include_ide")}
            </label>
            <SegmentedFilter<TrendMetric>
              label={t("costs_view.metric_label")}
              value={metric}
              onChange={setMetric}
              options={[
                { id: "cost", label: t("costs_view.metric_cost") },
                { id: "tokens", label: t("costs_view.metric_tokens") },
              ]}
            />
          </div>
        }
      />
      <div className="mt-3 h-[220px]">
        {chartError ? (
          <div role="alert" className="flex h-full items-center justify-center text-xs text-muted-foreground">
            {t("costs_view.load_error")}
          </div>
        ) : (
          <CostTrendChart
            series={chartData?.series ?? []}
            bucket={chartData?.bucket ?? bucket}
            metric={metric}
            currency={currency}
            eurPerUsd={eurPerUsd}
            loading={chartLoading}
          />
        )}
      </div>
    </Panel>
  );
}
