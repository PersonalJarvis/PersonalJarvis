import { Card } from "@/components/ui/card";
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
 * That window line now stands where the card's own title used to. Three
 * numbers labelled "Words", "Words per minute" and "Day streak" do not need a
 * heading saying "Your dictation" above them — it repeated what the tiles
 * already said — while the window they cover is the one fact nothing else on
 * the screen carries. The three decorative icons went for the same reason: at
 * --primary they rendered brighter than the numbers they decorated.
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
    <Card className="p-5" data-testid="dictation-stats">
      <p
        className="text-meta text-muted-foreground"
        data-testid="dictation-stats-window"
      >
        {windowLabel}
      </p>
      <div className="mt-stack grid gap-stack sm:grid-cols-3">
        <StatTile
          label={t("dictation.stats.words")}
          value={formatCount(stats.totals.words)}
          testId="dictation-stat-words"
        />
        <StatTile
          label={t("dictation.stats.wpm")}
          value={formatWpm(stats.totals.wpm)}
          testId="dictation-stat-wpm"
        />
        <StatTile
          label={t("dictation.stats.streak")}
          value={formatCount(stats.streak.current_days)}
          testId="dictation-stat-streak"
        />
      </div>
    </Card>
  );
}

/**
 * One tile: the number first, its name under it.
 *
 * --secondary, one step ABOVE the card it sits in. It used to be
 * `bg-background/40` inside `bg-card/60`, which rendered the three headline
 * numbers darker than their own container — a child below its parent, and on
 * exactly the elements someone opens this screen to read.
 */
function StatTile({
  label,
  value,
  testId,
}: {
  label: string;
  value: string;
  testId: string;
}) {
  return (
    <div className="rounded-md bg-secondary p-4">
      <p
        className="text-display tabular-nums text-foreground-strong"
        data-testid={testId}
      >
        {value}
      </p>
      <p className="mt-1 text-meta text-muted-foreground">{label}</p>
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
