import { useMemo, type ReactNode } from "react";

import { SkeletonBar } from "@/components/layout/PanelSkeleton";
import type { DictationStats } from "@/hooks/useDictation";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { VoiceGroup, VoiceSection } from "@/views/voice/voiceUi";

/**
 * The numbers worth knowing about your own dictation — how much, how fast,
 * how steadily, and today — as one strip of four, with the last two weeks
 * drawn as bars underneath.
 *
 * The totals are only "All time" when the never-pruned stats sidecar
 * answered. When the backend fell back to the rolling history window the
 * section says "Last N days": a 30-day slice labelled "All time" would quietly
 * understate every long-time user.
 *
 * Informational only. No goal, no nag, no popup.
 */
export function DictationStatsSection({ stats }: { stats: DictationStats }) {
  const t = useT();

  const windowLabel =
    stats.source === "lifetime"
      ? t("dictation.stats.window_lifetime")
      : t("dictation.stats.window_days").replace("{0}", String(stats.window.days));

  const days = useMemo(() => lastDays(stats.by_day, 14), [stats.by_day]);
  const peak = Math.max(1, ...days.map((d) => d.words));

  return (
    <VoiceSection
      title={t("dictation.stats.title")}
      description={<span data-testid="dictation-stats-window">{windowLabel}</span>}
      testId="dictation-stats"
    >
      <VoiceGroup>
        <StatGrid>
          <Stat
            label={t("dictation.stats.words")}
            value={formatCompact(stats.totals.words)}
            exact={formatCount(stats.totals.words)}
            testId="dictation-stat-words"
          />
          <Stat
            label={t("dictation.stats.wpm")}
            value={formatWpm(stats.totals.wpm)}
            testId="dictation-stat-wpm"
          />
          <Stat
            label={t("dictation.stats.streak")}
            value={formatCount(stats.streak.current_days)}
            testId="dictation-stat-streak"
          />
          <Stat
            label={t("dictation.group.today")}
            value={formatCount(stats.today.words)}
            testId="dictation-stat-today"
          />
        </StatGrid>

        <div className="px-4 py-4 sm:px-5">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>{t("dictation.stats.activity")}</span>
            {stats.streak.longest_days > 0 && (
              <span className="tabular-nums" data-testid="dictation-stat-best">
                {t("dictation.stats.best_streak").replace(
                  "{0}",
                  formatCount(stats.streak.longest_days),
                )}
              </span>
            )}
          </div>
          {/* Two weeks, today on the right. Height is relative to the busiest
              day in view; an empty day keeps a 2 px stub so the row still reads
              as fourteen days rather than as gaps. */}
          <div
            className="mt-3 flex h-12 items-end gap-1"
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
                    "flex-1 rounded-sm",
                    day.words === 0
                      ? "h-0.5 bg-secondary"
                      : today
                        ? "bg-foreground-strong"
                        : "bg-muted-foreground/40",
                  )}
                  style={day.words > 0 ? { height: `${pct}%` } : undefined}
                />
              );
            })}
          </div>
        </div>
      </VoiceGroup>
    </VoiceSection>
  );
}

/**
 * The same section at its real size while the numbers load — empty bars, never
 * zeros: a strip of zeros would read as a person who has never dictated.
 */
export function DictationStatsSkeleton() {
  const t = useT();
  return (
    <VoiceSection
      title={t("dictation.stats.title")}
      description={<SkeletonBar className="mt-1 h-3.5 w-20" />}
      testId="dictation-stats-loading"
    >
      <div aria-busy="true">
        <VoiceGroup>
          <StatGrid>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="flex flex-col gap-2 bg-card px-4 py-4 sm:px-5">
                <SkeletonBar className="h-3 w-16" />
                <SkeletonBar className="h-7 w-20" />
              </div>
            ))}
          </StatGrid>
          <div className="px-4 py-4 sm:px-5">
            <SkeletonBar className="h-3 w-24" />
            <SkeletonBar className="mt-3 h-12 w-full" />
          </div>
        </VoiceGroup>
      </div>
    </VoiceSection>
  );
}

/** Four tiles, 2×2 on a narrow window; the 1 px gap over --border draws the hairlines. */
function StatGrid({ children }: { children: ReactNode }) {
  return <dl className="grid grid-cols-2 gap-px bg-border sm:grid-cols-4">{children}</dl>;
}

/** One tile; the exact count sits in the title when the headline is compacted. */
function Stat({
  label,
  value,
  exact,
  testId,
}: {
  label: string;
  value: string;
  exact?: string;
  testId: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1 bg-card px-4 py-4 sm:px-5">
      <dt className="truncate text-xs text-muted-foreground">{label}</dt>
      <dd
        className="text-2xl font-semibold tabular-nums text-foreground-strong"
        title={exact && exact !== value ? exact : undefined}
        data-testid={testId}
      >
        {value}
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
      label: cursor.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
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
  return Math.round(value).toLocaleString();
}

/**
 * Short form for the headline number — "241.2K" — once it no longer fits at a
 * glance. Below ten thousand the exact count is short enough to show as is.
 */
function formatCompact(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (Math.abs(value) < 10_000) return formatCount(value);
  return new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

/** Words per minute, one decimal only while it is still small. */
function formatWpm(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "—";
  return value >= 10 ? String(Math.round(value)) : value.toFixed(1);
}
