/**
 * The spend curve — one bar per day (or hour), split by what it went to.
 *
 * Two modes, because "how much" has two honest answers here: money, stacked by
 * role, and tokens, stacked by direction (in / out / cache reads). They are
 * deliberately not one chart with a second axis — a dollar bar and a token bar
 * on the same grid invite reading a shape that is not there.
 */
import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { CostBucket } from "@/hooks/useCosts";
import { useT } from "@/i18n";
import {
  ROLE_ORDER,
  formatBucketFull,
  formatBucketTick,
  formatMoney,
  formatTokens,
  roleColor,
  roleLabelKey,
} from "./costFormat";

export type TrendMetric = "cost" | "tokens";

interface Props {
  series: CostBucket[];
  bucket: "day" | "hour";
  metric: TrendMetric;
  currency: "usd" | "eur";
  eurPerUsd: number;
  /** Bar the pointer selects; clicking it filters the section to that bucket. */
  onSelect?: (key: string) => void;
}

interface Point {
  key: string;
  total: number;
  tokens_in: number;
  tokens_out: number;
  tokens_cached: number;
  [role: string]: string | number;
}

const AXIS_TINT = "hsl(0 0% 50%)";
const TOKEN_COLORS = {
  tokens_in: "hsl(199 90% 62%)",
  tokens_out: "hsl(var(--primary))",
  tokens_cached: "hsl(268 72% 68%)",
} as const;

export function CostTrendChart({
  series,
  bucket,
  metric,
  currency,
  eurPerUsd,
  onSelect,
}: Props) {
  const t = useT();

  // Which role stacks actually occur — an empty stack still renders a legend
  // entry in Recharts, and a legend full of zeroes reads as missing data.
  const roles = useMemo(() => {
    const present = new Set<string>();
    for (const b of series) for (const k of Object.keys(b.breakdown ?? {})) present.add(k);
    return ROLE_ORDER.filter((r) => present.has(r));
  }, [series]);

  const data: Point[] = useMemo(
    () =>
      series.map((b) => {
        const point: Point = {
          key: b.key,
          total: b.cost_usd,
          tokens_in: b.tokens_in,
          tokens_out: b.tokens_out,
          tokens_cached: b.tokens_cached,
        };
        for (const role of roles) point[role] = b.breakdown?.[role] ?? 0;
        return point;
      }),
    [series, roles],
  );

  if (data.length === 0) {
    return (
      <div className="flex h-full min-h-[180px] items-center justify-center text-xs text-muted-foreground">
        {t("costs_view.chart_empty")}
      </div>
    );
  }

  const stacks: { dataKey: string; color: string; labelKey: string }[] =
    metric === "cost"
      ? roles.map((r) => ({ dataKey: r, color: roleColor(r), labelKey: roleLabelKey(r) }))
      : [
          {
            dataKey: "tokens_in",
            color: TOKEN_COLORS.tokens_in,
            labelKey: "costs_view.tokens_in",
          },
          {
            dataKey: "tokens_out",
            color: TOKEN_COLORS.tokens_out,
            labelKey: "costs_view.tokens_out",
          },
          {
            dataKey: "tokens_cached",
            color: TOKEN_COLORS.tokens_cached,
            labelKey: "costs_view.tokens_cached",
          },
        ];

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <ResponsiveContainer
        width="100%"
        height="100%"
        // Same guard as the board's trend chart: without a positive floor the
        // first measure is 0x0 and the bars bake in a collapsed scale.
        minHeight={172}
        minWidth={0}
        initialDimension={{ width: 640, height: 200 }}
      >
        <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: 4 }} barCategoryGap="18%">
          <CartesianGrid vertical={false} stroke="hsl(0 0% 50% / 0.14)" strokeDasharray="2 4" />
          <XAxis
            dataKey="key"
            tickFormatter={(k: string) => formatBucketTick(k, bucket)}
            tick={{ fill: AXIS_TINT, fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            minTickGap={28}
            dy={4}
          />
          <YAxis
            width={52}
            tick={{ fill: AXIS_TINT, fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) =>
              metric === "cost" ? formatMoney(v, eurPerUsd, currency) : formatTokens(v)
            }
          />
          <Tooltip
            cursor={{ fill: "hsl(0 0% 50% / 0.08)" }}
            content={
              <TrendTooltip
                bucket={bucket}
                metric={metric}
                currency={currency}
                eurPerUsd={eurPerUsd}
                stacks={stacks}
                t={t}
              />
            }
          />
          {stacks.map((s, i) => (
            <Bar
              key={s.dataKey}
              dataKey={s.dataKey}
              stackId="a"
              fill={s.color}
              isAnimationActive={false}
              // Only the top segment gets the rounded cap, so a stack reads as
              // one bar rather than a pile of separate pills.
              radius={i === stacks.length - 1 ? [3, 3, 0, 0] : undefined}
              onClick={(entry: { payload?: Point }) =>
                onSelect && entry?.payload ? onSelect(String(entry.payload.key)) : undefined
              }
            >
              {onSelect
                ? data.map((d) => <Cell key={d.key} cursor="pointer" />)
                : null}
            </Bar>
          ))}
        </BarChart>
      </ResponsiveContainer>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-1">
        {stacks.map((s) => (
          <span key={s.dataKey} className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span className="h-2 w-2 rounded-[2px]" style={{ background: s.color }} />
            {t(s.labelKey)}
          </span>
        ))}
      </div>
    </div>
  );
}

interface TooltipItem {
  dataKey: string;
  value: number;
}

function TrendTooltip(props: {
  active?: boolean;
  payload?: TooltipItem[];
  label?: string;
  bucket: "day" | "hour";
  metric: TrendMetric;
  currency: "usd" | "eur";
  eurPerUsd: number;
  stacks: { dataKey: string; color: string; labelKey: string }[];
  t: (key: string) => string;
}) {
  const { active, payload, label, bucket, metric, currency, eurPerUsd, stacks, t } = props;
  if (!active || !payload || payload.length === 0) return null;
  const total = payload.reduce((sum, p) => sum + (p.value ?? 0), 0);
  const fmt = (v: number) =>
    metric === "cost" ? formatMoney(v, eurPerUsd, currency) : formatTokens(v);

  return (
    <div className="min-w-[170px] rounded-lg border border-border bg-popover/95 px-3 py-2 text-xs backdrop-blur">
      <div className="mb-1.5 font-medium text-foreground">
        {formatBucketFull(String(label ?? ""), bucket)}
      </div>
      {stacks.map((s) => {
        const value = payload.find((p) => p.dataKey === s.dataKey)?.value ?? 0;
        if (!value) return null;
        return (
          <div key={s.dataKey} className="flex items-center gap-2 py-0.5">
            <span className="h-2 w-2 rounded-[2px]" style={{ background: s.color }} />
            <span className="text-muted-foreground">{t(s.labelKey)}</span>
            <span className="ml-auto font-semibold tabular-nums text-foreground">{fmt(value)}</span>
          </div>
        );
      })}
      <div className="mt-1.5 flex items-center gap-2 border-t border-border pt-1.5">
        <span className="text-muted-foreground">{t("costs_view.total")}</span>
        <span className="ml-auto font-semibold tabular-nums text-foreground">{fmt(total)}</span>
      </div>
    </div>
  );
}
