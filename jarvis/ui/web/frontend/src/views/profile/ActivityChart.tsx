/**
 * The profile's activity block: one bar per week, beside three plain figures.
 *
 * Left: the last seven days (with the change against the seven before), the
 * mean of the finished weeks, and the busiest weekday. Right: the weeks since
 * the first active one, one hue, a recessive two-line grid. Hovering a bar
 * names its week and its count; every other bar steps back. The week still
 * running is drawn lighter, so a Monday never reads as a collapse.
 *
 * Four tabs pick the series (everything, voice, chats, agents). The axis is
 * shared across tabs; only the heights and the figures change.
 */
import { useMemo, useState } from "react";

import { useBoardInsights } from "@/hooks/useBoardInsights";
import { fill, useT, useUiLanguage } from "@/i18n";
import { cn } from "@/lib/utils";
import {
  SERIES_IDS,
  summarize,
  weekDelta,
  type SeriesId,
  type WeekBar,
} from "@/views/profile/activity";

const TAB_KEY: Record<SeriesId, string> = {
  all: "tab_all",
  voice: "tab_voice",
  chats: "tab_chats",
  agents: "tab_agents",
};

const UNIT_KEY: Record<SeriesId, string> = {
  all: "unit_all",
  voice: "unit_voice",
  chats: "unit_chats",
  agents: "unit_agents",
};

/** A Monday, so `weekday` index 0…6 maps onto Monday…Sunday. */
const A_MONDAY = new Date(2024, 0, 1);

export function ActivityChart() {
  const t = useT();
  const ui = useUiLanguage();
  const insights = useBoardInsights();
  const [series, setSeries] = useState<SeriesId>("all");
  const [hovered, setHovered] = useState<number | null>(null);

  const days = insights.data?.days;
  const summary = useMemo(() => (days ? summarize(days, series) : null), [days, series]);

  const num = (n: number) => n.toLocaleString(ui);
  const unit = t(`profile_view.${UNIT_KEY[series]}`);

  return (
    <section data-testid="profile-activity" aria-labelledby="profile-activity-title" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 border-b border-border">
        <h2 id="profile-activity-title" className="pb-2.5 text-base font-semibold text-foreground-strong">
          {t("profile_view.activity_title")}
        </h2>
        <div role="tablist" aria-label={t("profile_view.activity_title")} className="flex gap-5">
          {SERIES_IDS.map((id) => {
            const on = id === series;
            return (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={on}
                onClick={() => {
                  setSeries(id);
                  setHovered(null);
                }}
                className={cn(
                  "-mb-px border-b-2 pb-2.5 text-base transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  on
                    ? "border-foreground-strong font-medium text-foreground-strong"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                {t(`profile_view.${TAB_KEY[id]}`)}
              </button>
            );
          })}
        </div>
      </div>

      {!summary ? (
        <ActivitySkeleton label={t("common.loading")} />
      ) : summary.weeks.length === 0 ? (
        <p className="py-10 text-center text-base text-muted-foreground">
          {t("profile_view.activity_empty")}
        </p>
      ) : (
        <div className="flex flex-col gap-6 sm:flex-row sm:items-stretch sm:gap-7">
          <Figures
            last7={num(summary.last7)}
            unit={unit}
            delta={deltaText(t, weekDelta(summary.last7, summary.prev7), summary.last7)}
            avgWeek={summary.avgWeek === null ? "–" : num(summary.avgWeek)}
            topWeekday={
              summary.topWeekday === null
                ? "–"
                : new Date(
                    A_MONDAY.getFullYear(),
                    A_MONDAY.getMonth(),
                    A_MONDAY.getDate() + summary.topWeekday,
                  ).toLocaleDateString(ui, { weekday: "long" })
            }
            labels={{
              last7: t("profile_view.activity_last7"),
              avg: t("profile_view.activity_avg_week"),
              top: t("profile_view.activity_top_day"),
            }}
          />
          <Bars
            weeks={summary.weeks}
            axisMax={summary.axisMax}
            hovered={hovered}
            onHover={setHovered}
            ui={ui}
            num={num}
            unit={unit}
            runningLabel={t("profile_view.activity_running")}
            label={fill(t("profile_view.activity_chart_label"), {
              0: unit,
              1: summary.weeks[0].start.toLocaleDateString(ui, { day: "numeric", month: "short", year: "numeric" }),
            })}
          />
        </div>
      )}
    </section>
  );
}

function deltaText(t: (key: string) => string, delta: number | null, last7: number): string {
  if (delta === null) return last7 > 0 ? t("profile_view.delta_new") : "";
  if (delta === 0) return t("profile_view.delta_same");
  return fill(t(delta > 0 ? "profile_view.delta_up" : "profile_view.delta_down"), {
    0: Math.abs(delta),
  });
}

function Figures({
  last7,
  unit,
  delta,
  avgWeek,
  topWeekday,
  labels,
}: {
  last7: string;
  unit: string;
  delta: string;
  avgWeek: string;
  topWeekday: string;
  labels: { last7: string; avg: string; top: string };
}) {
  return (
    <div className="flex shrink-0 flex-col justify-between gap-4 sm:w-48">
      <div className="flex flex-col gap-1">
        <span className="text-sm text-muted-foreground">{labels.last7}</span>
        <span className="flex min-w-0 items-baseline gap-1.5">
          <span
            data-testid="activity-last7"
            className="font-display text-2xl tabular-nums leading-tight text-foreground-strong"
          >
            {last7}
          </span>
          <span className="truncate text-sm text-muted-foreground">{unit}</span>
        </span>
        {delta && <span className="text-sm text-muted-foreground">{delta}</span>}
      </div>
      <div className="grid grid-cols-2 gap-3 border-t border-border pt-3">
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="truncate text-sm text-muted-foreground">{labels.avg}</span>
          <span className="truncate text-base font-medium tabular-nums text-foreground-strong">{avgWeek}</span>
        </span>
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="truncate text-sm text-muted-foreground">{labels.top}</span>
          <span className="truncate text-base font-medium text-foreground-strong">{topWeekday}</span>
        </span>
      </div>
    </div>
  );
}

function Bars({
  weeks,
  axisMax,
  hovered,
  onHover,
  ui,
  num,
  unit,
  runningLabel,
  label,
}: {
  weeks: WeekBar[];
  axisMax: number;
  hovered: number | null;
  onHover: (index: number | null) => void;
  ui: string;
  num: (n: number) => string;
  unit: string;
  runningLabel: string;
  label: string;
}) {
  const heightPct = (total: number) => (total > 0 ? Math.max(2.5, (total / axisMax) * 100) : 0);
  const short = (d: Date) => d.toLocaleDateString(ui, { day: "numeric", month: "short" });

  // A month name sits under the week that holds that month's first day.
  const months = useMemo(() => {
    let last = -1;
    return weeks.map((w) => {
      for (let k = 0; k < 7; k++) {
        const d = new Date(w.start.getFullYear(), w.start.getMonth(), w.start.getDate() + k);
        if (d > w.end) break;
        if (d.getDate() === 1 && d.getMonth() !== last) {
          last = d.getMonth();
          return d.toLocaleDateString(ui, { month: "short" });
        }
      }
      return "";
    });
  }, [weeks, ui]);

  const tip = hovered !== null ? weeks[hovered] : null;
  const tipLeft = hovered !== null ? Math.min(Math.max(((hovered + 0.5) / weeks.length) * 100, 10), 90) : 0;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1.5">
      <div className="relative h-[clamp(5rem,16vh,9rem)]">
        <div aria-hidden className="absolute inset-x-0 top-0 mr-10 border-t border-dashed border-border" />
        <div aria-hidden className="absolute inset-x-0 top-1/2 mr-10 border-t border-dashed border-border" />
        <div aria-hidden className="absolute inset-x-0 bottom-0 mr-10 border-t border-border-strong" />
        <span aria-hidden className="absolute right-0 top-0 -translate-y-1/2 text-xs tabular-nums text-muted-foreground">
          {num(axisMax)}
        </span>
        {/* A half like 12.5 reads as noise; the line alone carries it. */}
        {Number.isInteger(axisMax / 2) && (
          <span aria-hidden className="absolute right-0 top-1/2 -translate-y-1/2 text-xs tabular-nums text-muted-foreground">
            {num(axisMax / 2)}
          </span>
        )}

        <div
          role="img"
          aria-label={label}
          data-testid="activity-bars"
          onMouseLeave={() => onHover(null)}
          className="absolute inset-y-0 left-0 right-10 flex items-end gap-0.5"
        >
          {weeks.map((w, i) => {
            const dim = (hovered !== null && hovered !== i) || (hovered === null && w.partial);
            return (
              <div
                key={w.start.getTime()}
                onMouseEnter={() => onHover(i)}
                className="flex h-full min-w-0 flex-1 items-end"
              >
                <span
                  className={cn(
                    "block w-full rounded-t-[4px] transition-colors duration-150",
                    dim ? "bg-[rgb(var(--accent-rgb)/0.38)]" : "bg-accent",
                  )}
                  style={{ height: `${heightPct(w.total)}%` }}
                />
              </div>
            );
          })}
        </div>

        {tip && (
          <div
            role="status"
            className="pointer-events-none absolute z-10 flex -translate-x-1/2 -translate-y-full flex-col gap-0.5 whitespace-nowrap rounded-lg bg-popover px-2.5 py-2 shadow-float"
            style={{
              left: `calc((100% - 2.5rem) * ${tipLeft / 100})`,
              top: `calc(${100 - heightPct(tip.total)}% - 0.5rem)`,
            }}
          >
            <span className="text-xs text-muted-foreground">
              {short(tip.start)} – {short(tip.end)}
              {tip.partial && ` · ${runningLabel}`}
            </span>
            <span className="text-sm font-medium tabular-nums text-foreground-strong">
              {num(tip.total)} {unit}
            </span>
          </div>
        )}
      </div>

      <div aria-hidden className="mr-10 flex gap-0.5">
        {months.map((m, i) => (
          <span
            key={i}
            className="h-4 min-w-0 flex-1 overflow-visible whitespace-nowrap text-xs leading-4 text-muted-foreground"
          >
            {m}
          </span>
        ))}
      </div>
    </div>
  );
}

function ActivitySkeleton({ label }: { label: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label} className="flex gap-7">
      <div className="flex w-48 shrink-0 flex-col gap-2">
        <div className="h-3.5 w-20 animate-pulse rounded-full bg-secondary" />
        <div className="h-7 w-24 animate-pulse rounded-md bg-secondary" />
      </div>
      <div className="h-[clamp(5rem,16vh,9rem)] flex-1 animate-pulse rounded-lg bg-secondary" />
    </div>
  );
}
