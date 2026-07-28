import { Flame, Gauge, Type } from "lucide-react";

import type { DictationStats } from "@/hooks/useDictation";
import { useT } from "@/i18n";

/**
 * The three numbers worth knowing about your own dictation: how many words you
 * have spoken, how fast you speak them, and how many days in a row you have
 * used it.
 *
 * The honesty rule this component exists to enforce: the totals are only
 * all-time when the never-pruned stats sidecar answered. When the backend fell
 * back to deriving them from the rolling history window, the strip says "Last
 * N days" instead — a 30-day slice labelled "All time" would quietly understate
 * every long-time user's numbers.
 *
 * Informational only. No goal, no nag, no popup.
 */
export function DictationStatsBar({ stats }: { stats: DictationStats }) {
  const t = useT();

  const windowLabel =
    stats.source === "lifetime"
      ? t("dictation.stats.window_lifetime")
      : t("dictation.stats.window_days").replace(
          "{0}",
          String(stats.window.days),
        );

  return (
    <div
      className="rounded-lg border border-border bg-card/60 p-4"
      data-testid="dictation-stats"
    >
      <div className="flex items-center justify-between gap-3">
        <h4 className="font-display text-sm font-semibold">
          {t("dictation.stats.title")}
        </h4>
        <span
          className="rounded-full border border-border bg-muted/60 px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
          data-testid="dictation-stats-window"
        >
          {windowLabel}
        </span>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <StatTile
          icon={<Type className="h-3.5 w-3.5 text-primary" />}
          label={t("dictation.stats.words")}
          value={formatCount(stats.totals.words)}
          testId="dictation-stat-words"
        />
        <StatTile
          icon={<Gauge className="h-3.5 w-3.5 text-primary" />}
          label={t("dictation.stats.wpm")}
          value={formatWpm(stats.totals.wpm)}
          testId="dictation-stat-wpm"
        />
        <StatTile
          icon={<Flame className="h-3.5 w-3.5 text-primary" />}
          label={t("dictation.stats.streak")}
          value={formatCount(stats.streak.current_days)}
          testId="dictation-stat-streak"
        />
      </div>
    </div>
  );
}

function StatTile({
  icon,
  label,
  value,
  testId,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  testId: string;
}) {
  return (
    <div className="rounded-md border border-border/60 bg-background/40 p-3">
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        {icon}
        <span className="truncate">{label}</span>
      </div>
      <p
        className="mt-1 font-display text-xl font-semibold tabular-nums"
        data-testid={testId}
      >
        {value}
      </p>
    </div>
  );
}

/** Locale-grouped integer; a non-finite server value degrades to a dash. */
function formatCount(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return Math.round(value).toLocaleString();
}

/** Words per minute, one decimal only while it is still small. */
function formatWpm(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "—";
  return value >= 10 ? String(Math.round(value)) : value.toFixed(1);
}
