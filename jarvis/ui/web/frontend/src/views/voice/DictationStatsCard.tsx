import { useMemo } from "react";

import type { DictationStats } from "@/hooks/useDictation";
import { useI18nStore, useT } from "@/i18n";
import { localeForUiLanguage } from "@/components/runs/format";
import { cn } from "@/lib/utils";

/**
 * The side card with the three numbers worth knowing about your own
 * dictation — how much, how fast, how steadily — and the last two weeks drawn
 * as a row of bars underneath.
 *
 * Each number is set large with its unit beside it ("241.2K words") so the
 * card reads as three short sentences instead of a grid of labelled tiles.
 * The exact count lives in the title attribute: the compact form is for
 * glancing, the full number is one hover away.
 *
 * The honesty rule from before still holds: the totals are only "All time"
 * when the never-pruned stats sidecar answered. When the backend fell back to
 * the rolling history window, the card says "Last N days" — a 30-day slice
 * labelled "All time" would quietly understate every long-time user.
 *
 * Informational only. No goal, no nag, no popup.
 */
export function DictationStatsCard({ stats }: { stats: DictationStats }) {
  const t = useT();

  const windowLabel =
    stats.source === "lifetime"
      ? t("dictation.stats.window_lifetime")
      : t("dictation.stats.window_days").replace("{0}", String(stats.window.days));

  const days = useMemo(() => lastDays(stats.by_day, 14), [stats.by_day]);
  const peak = Math.max(1, ...days.map((d) => d.words));

  return (
    <section
      className="rounded-xl border border-border bg-card p-5 shadow-rim"
      data-testid="dictation-stats"
    >
      <p
        className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground"
        data-testid="dictation-stats-window"
      >
        {windowLabel}
      </p>

      <dl className="mt-4 flex flex-col gap-3">
        <Stat
          value={formatCompact(stats.totals.words)}
          exact={formatCount(stats.totals.words)}
          unit={t("dictation.stats.words_unit")}
          label={t("dictation.stats.words")}
          testId="dictation-stat-words"
        />
        <Stat
          value={formatWpm(stats.totals.wpm)}
          unit={t("dictation.stats.wpm_unit")}
          label={t("dictation.stats.wpm")}
          testId="dictation-stat-wpm"
        />
        <Stat
          value={formatCount(stats.streak.current_days)}
          unit={t("dictation.stats.streak_unit")}
          label={t("dictation.stats.streak")}
          testId="dictation-stat-streak"
        />
      </dl>

      <div className="mt-5 border-t border-border pt-4">
        <div className="flex items-baseline justify-between gap-3 text-xs text-muted-foreground">
          <span>{t("dictation.stats.activity")}</span>
          {stats.streak.longest_days > 0 && (
            <span data-testid="dictation-stat-best">
              {t("dictation.stats.best_streak").replace(
                "{0}",
                formatCount(stats.streak.longest_days),
              )}
            </span>
          )}
        </div>
        {/* Two weeks as bars, today on the right. Height is relative to the
            busiest day in view; a day with nothing keeps a 2px stub so the
            row still reads as fourteen days rather than as gaps. */}
        <div
          className="mt-3 flex h-14 items-end gap-[3px]"
          role="img"
          aria-label={t("dictation.stats.activity")}
          data-testid="dictation-activity"
        >
          {days.map((day, i) => {
            const today = i === days.length - 1;
            const pct = day.words > 0 ? Math.max(8, (day.words / peak) * 100) : 0;
            return (
              <div
                key={day.date}
                title={t("dictation.stats.activity_day")
                  .replace("{0}", day.label)
                  .replace("{1}", formatCount(day.words))}
                className={cn(
                  "min-h-[2px] flex-1 rounded-[3px] transition-colors",
                  day.words === 0
                    ? "bg-secondary"
                    : today
                      ? "bg-foreground-strong"
                      : "bg-muted-foreground/45 hover:bg-muted-foreground/70",
                )}
                style={{ height: day.words > 0 ? `${pct}%` : undefined }}
              />
            );
          })}
        </div>
        <p className="mt-3 text-sm text-foreground-secondary">
          {t("dictation.stats.today").replace("{0}", formatCount(stats.today.words))}
        </p>
      </div>
    </section>
  );
}

/** One "241.2K words" line; the long label is kept for screen readers. */
function Stat({
  value,
  exact,
  unit,
  label,
  testId,
}: {
  value: string;
  exact?: string;
  unit: string;
  label: string;
  testId: string;
}) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="sr-only">{label}</dt>
      <dd className="flex items-baseline gap-2">
        <span
          className="text-[28px] font-semibold leading-[32px] tracking-[-0.02em] tabular-nums text-foreground-strong"
          title={exact && exact !== value ? exact : undefined}
          data-testid={testId}
        >
          {value}
        </span>
        <span className="text-base text-muted-foreground">{unit}</span>
      </dd>
    </div>
  );
}

interface DayBar {
  /** Local `YYYY-MM-DD`. */
  date: string;
  /** Locale-formatted short date for the tooltip. */
  label: string;
  words: number;
}

/**
 * The last `count` local calendar days, oldest first, each with its word count
 * from `by_day` (a day the backend did not list is a zero day).
 */
function lastDays(byDay: DictationStats["by_day"], count: number): DayBar[] {
  const words = new Map<string, number>();
  for (const day of byDay ?? []) words.set(day.date, day.words);
  const out: DayBar[] = [];
  const cursor = new Date();
  cursor.setHours(12, 0, 0, 0);
  cursor.setDate(cursor.getDate() - (count - 1));
  for (let i = 0; i < count; i += 1) {
    const key = localKey(cursor);
    out.push({
      date: key,
      label: cursor.toLocaleDateString(localeForUiLanguage(useI18nStore.getState().ui), { month: "short", day: "numeric" }),
      words: words.get(key) ?? 0,
    });
    cursor.setDate(cursor.getDate() + 1);
  }
  return out;
}

function localKey(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/** Locale-grouped integer; a non-finite server value degrades to a dash. */
function formatCount(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return Math.round(value).toLocaleString(localeForUiLanguage(useI18nStore.getState().ui));
}

/**
 * Short form for the headline number — "241.2K" — once it no longer fits at a
 * glance. Below ten thousand the exact count is short enough to show as is.
 */
function formatCompact(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (Math.abs(value) < 10_000) return formatCount(value);
  return new Intl.NumberFormat(localeForUiLanguage(useI18nStore.getState().ui), {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

/** Words per minute, one decimal only while it is still small. */
function formatWpm(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "—";
  return value >= 10 ? String(Math.round(value)) : value.toFixed(1);
}
