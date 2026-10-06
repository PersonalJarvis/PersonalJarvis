/**
 * Activity math for the profile's week chart — pure and unit-tested.
 *
 * The chart shows one bar per Monday-start week, from the first week that
 * holds any activity at all (in any series), so switching between series
 * never moves the time axis. The newest week is usually still running; it is
 * flagged so the chart can draw it as unfinished instead of as a drop.
 */
import type { InsightsDay } from "@/hooks/useBoardInsights";

export type SeriesId = "all" | "voice" | "chats" | "agents";

export const SERIES_IDS: readonly SeriesId[] = ["all", "voice", "chats", "agents"];

export interface WeekBar {
  start: Date;
  end: Date;
  total: number;
  /** Fewer than seven days so far: the current week. */
  partial: boolean;
}

export interface ActivitySummary {
  weeks: WeekBar[];
  /** The top of the value axis: the busiest week rounded up to 1, 2, 2.5 or 5 × 10ⁿ. */
  axisMax: number;
  last7: number;
  prev7: number;
  /** Mean over finished weeks; null before the first week has finished. */
  avgWeek: number | null;
  /** 0 = Monday … 6 = Sunday; null when the series is empty. */
  topWeekday: number | null;
}

export function dayValue(day: InsightsDay, series: SeriesId): number {
  const voice = day.voice_sessions + day.dictations;
  if (series === "voice") return voice;
  if (series === "chats") return day.chat_messages;
  if (series === "agents") return day.agent_sessions;
  return voice + day.chat_messages + day.agent_sessions;
}

/** "2026-10-05" as local midnight; the backend's dates are local days. */
export function parseDay(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, (m ?? 1) - 1, d ?? 1);
}

/** Monday = 0 … Sunday = 6. */
export function weekdayIndex(date: Date): number {
  return (date.getDay() + 6) % 7;
}

export function niceCeil(value: number): number {
  if (value <= 0) return 1;
  const magnitude = Math.pow(10, Math.floor(Math.log10(value)));
  for (const step of [1, 2, 2.5, 5, 10]) {
    if (step * magnitude >= value) return step * magnitude;
  }
  return 10 * magnitude;
}

export function summarize(days: readonly InsightsDay[], series: SeriesId): ActivitySummary {
  const values = days.map((d) => dayValue(d, series));
  const firstActive = days.findIndex((d) => dayValue(d, "all") > 0);

  const weeks: WeekBar[] = [];
  if (firstActive >= 0) {
    // The Monday of the first active week; it can sit before the first day
    // the backend sent, in which case that week simply starts with the data.
    const firstMonday = firstActive - weekdayIndex(parseDay(days[firstActive].date));
    for (let monday = firstMonday; monday < days.length; monday += 7) {
      const from = Math.max(0, monday);
      const to = Math.min(monday + 6, days.length - 1);
      let total = 0;
      for (let i = from; i <= to; i++) total += values[i];
      weeks.push({
        start: parseDay(days[from].date),
        end: parseDay(days[to].date),
        total,
        partial: monday + 6 > days.length - 1,
      });
    }
  }

  const sum = (from: number, to: number) =>
    values.slice(Math.max(0, from), Math.max(0, to)).reduce((acc, v) => acc + v, 0);
  const n = values.length;
  const finished = weeks.filter((w) => !w.partial);

  const byWeekday = [0, 0, 0, 0, 0, 0, 0];
  days.forEach((d, i) => {
    byWeekday[weekdayIndex(parseDay(d.date))] += values[i];
  });
  const busiest = Math.max(...byWeekday);

  return {
    weeks,
    axisMax: niceCeil(Math.max(0, ...weeks.map((w) => w.total))),
    last7: sum(n - 7, n),
    prev7: sum(n - 14, n - 7),
    avgWeek: finished.length
      ? Math.round(finished.reduce((acc, w) => acc + w.total, 0) / finished.length)
      : null,
    topWeekday: busiest > 0 ? byWeekday.indexOf(busiest) : null,
  };
}

/** Week-over-week change in whole percent; null when there is no prior week to compare. */
export function weekDelta(last7: number, prev7: number): number | null {
  if (prev7 <= 0) return null;
  return Math.round(((last7 - prev7) / prev7) * 100);
}
