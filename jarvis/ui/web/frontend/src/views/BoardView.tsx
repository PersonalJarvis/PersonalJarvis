import { useState } from "react";
import { Flame, Loader2, RefreshCw, Share2, Sparkles } from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { BoardCard } from "@/components/board/BoardCard";
import { ShareDialog } from "@/components/board/ShareDialog";
import { CategoryUsage } from "@/components/board/CategoryUsage";
import { ActivityHeatmap, HeatmapScale } from "@/components/board/ActivityHeatmap";
import {
  WordsTrendChart,
  TREND_JARVIS,
  TREND_YOU,
} from "@/components/board/WordsTrendChart";
import {
  useBoardCategories,
  useBoardHeatmap,
  useBoardRefresh,
  useBoardSummary,
} from "@/hooks/useBoard";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

export function BoardView() {
  const t = useT();
  const summary = useBoardSummary();
  const heatmap = useBoardHeatmap(365);
  const categories = useBoardCategories();
  const refresh = useBoardRefresh();
  const [shareOpen, setShareOpen] = useState(false);

  const s = summary.data;
  const userWords = s?.totals.user_words ?? 0;
  const jarvisWords = s?.totals.jarvis_words ?? 0;
  const sessions = s?.totals.session_count ?? 0;
  const convHours = s?.totals.conversation_hours ?? 0;
  const streak = s?.streak_days ?? 0;
  const longest = s?.longest_streak ?? 0;
  const activeDays = s?.totals.active_days ?? 0;
  const avgWords = sessions ? Math.round(userWords / sessions) : 0;
  const ratio = userWords ? jarvisWords / userWords : 0;

  const nf = (n: number) => n.toLocaleString();
  const loading = summary.isLoading;

  const metrics = [
    {
      label: t("board_view.hero.you_spoke"),
      dot: TREND_YOU,
      value: loading ? "—" : nf(userWords),
      sub: t("board_view.hero.you_spoke_sub"),
    },
    {
      label: t("board_view.hero.jarvis_spoke"),
      dot: TREND_JARVIS,
      value: loading ? "—" : nf(jarvisWords),
      sub: t("board_view.hero.jarvis_spoke_sub"),
    },
    {
      label: t("board_view.hero.streak"),
      value: loading ? "—" : streak > 0 ? String(streak) : "—",
      sub:
        longest > 0
          ? plural(t, "board_view.longest_streak", longest)
          : plural(t, "board_view.hero.streak_sub", activeDays),
    },
    {
      label: t("board_view.hero.talk_time"),
      value: loading ? "—" : `${convHours.toFixed(1)} h`,
      sub: plural(t, "board_view.hero.talk_time_sub", sessions),
    },
  ];

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Sparkles className="h-4 w-4 text-primary" />}
        title={t("board_view.title")}
        subtitle={t("board_view.subtitle")}
        right={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShareOpen(true)}
              disabled={loading || !s}
              className={cn(
                "inline-flex items-center gap-2 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-xs font-medium transition-colors",
                "hover:border-primary/40 hover:bg-primary/[0.06]",
                (loading || !s) && "opacity-50",
              )}
              title={t("board_view.share.button_tooltip")}
              data-testid="board-share-button"
            >
              <Share2 className="h-3.5 w-3.5" />
              {t("board_view.share.button")}
            </button>
            <button
              type="button"
              onClick={() => refresh.mutate()}
              disabled={refresh.isPending}
              className={cn(
                "inline-flex items-center gap-2 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-xs font-medium transition-colors",
                "hover:border-primary/40 hover:bg-primary/[0.06]",
                refresh.isPending && "opacity-60",
              )}
              title={t("board_view.refresh_tooltip")}
            >
              <RefreshCw className={cn("h-3.5 w-3.5", refresh.isPending && "animate-spin")} />
              {t("board_view.refresh")}
            </button>
          </div>
        }
      />

      <div className="flex-1 overflow-y-auto scrollbar-jarvis">
        <div className="mx-auto flex max-w-[1440px] flex-col gap-4 p-5 lg:gap-5 lg:p-6">
          {summary.error && (
            <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
              {t("board_view.load_error")}
            </div>
          )}

          {/* ── Hero band: four key numbers, hairline dividers ───────── */}
          <BoardCard glow>
            <div className="grid grid-cols-2 lg:grid-cols-4">
              {metrics.map((m, i) => (
                <Metric
                  key={i}
                  label={m.label}
                  value={m.value}
                  sub={m.sub}
                  dot={m.dot}
                  className={DIVIDERS[i]}
                />
              ))}
            </div>
          </BoardCard>

          {/* Slim caption line of secondary stats. */}
          {!loading && s && (
            <p className="-mt-1 px-1 text-[11px] text-muted-foreground">
              {t("board_view.micro.avg_words").replace("{0}", nf(avgWords))}
              <Dot />
              {t("board_view.micro.ratio").replace("{0}", ratio.toFixed(1))}
              {s.totals.first_day && (
                <>
                  <Dot />
                  {t("board_view.micro.active_since").replace("{0}", formatDay(s.totals.first_day))}
                </>
              )}
            </p>
          )}

          {/* ── Words-over-time chart + category usage ───────────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.55fr_1fr] lg:gap-5">
            <BoardCard className="flex flex-col p-5">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <h3 className="font-display text-sm font-semibold">
                    {t("board_view.chart_title")}
                  </h3>
                  <p className="text-xs text-muted-foreground">
                    {t("board_view.activity_subtitle")}
                  </p>
                </div>
                <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
                  <LegendDot color={TREND_YOU} label={t("board_view.hero.you_spoke")} />
                  <LegendDot color={TREND_JARVIS} label={t("board_view.hero.jarvis_spoke")} />
                </div>
              </div>
              <div className="h-44 lg:h-52">
                {heatmap.data ? (
                  <WordsTrendChart cells={heatmap.data.cells} />
                ) : (
                  <Skeleton className="h-full" />
                )}
              </div>
            </BoardCard>

            <BoardCard className="flex flex-col p-5">
              <div className="mb-3">
                <h3 className="font-display text-sm font-semibold">
                  {t("board_view.categories_title")}
                </h3>
                <p className="text-xs text-muted-foreground">
                  {t("board_view.categories_subtitle")}
                </p>
              </div>
              <div className="flex flex-1 flex-col justify-center">
                {categories.data ? (
                  <CategoryUsage data={categories.data} />
                ) : (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    {t("board_view.loading")}
                  </div>
                )}
              </div>
            </BoardCard>
          </div>

          {/* ── Activity calendar ────────────────────────────────────── */}
          <BoardCard className="p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="font-display text-sm font-semibold">
                  {t("board_view.activity_title")}
                </h3>
                <p className="text-xs text-muted-foreground">
                  {t("board_view.activity_heatmap_hint")}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {streak > 0 && (
                  <span className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/[0.08] px-2.5 py-1 text-[11px] font-medium text-primary">
                    <Flame className="h-3 w-3" />
                    {plural(t, "board_view.activity_streak_badge", streak)}
                  </span>
                )}
                {longest > 0 && (
                  <span className="rounded-full border border-white/[0.08] bg-white/[0.03] px-2.5 py-1 text-[11px] text-muted-foreground">
                    {plural(t, "board_view.longest_streak", longest)}
                  </span>
                )}
              </div>
            </div>
            {heatmap.data ? (
              <div className="space-y-3">
                <div className="overflow-x-auto pb-1">
                  <ActivityHeatmap cells={heatmap.data.cells} />
                </div>
                <HeatmapScale />
              </div>
            ) : (
              <Skeleton className="h-28" />
            )}
          </BoardCard>
        </div>
      </div>

      {s && (
        <ShareDialog
          open={shareOpen}
          onOpenChange={setShareOpen}
          stats={{
            userWords,
            jarvisWords,
            conversationHours: convHours,
            sessionCount: sessions,
            longestStreak: longest,
          }}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
// Building blocks
// ----------------------------------------------------------------------

// Per-index hairline dividers for a 2-col (mobile) / 4-col (desktop) grid.
const DIVIDERS = [
  "",
  "border-l border-white/[0.06]",
  "border-t border-white/[0.06] lg:border-t-0 lg:border-l",
  "border-l border-t border-white/[0.06] lg:border-t-0",
];

function Metric({
  label,
  value,
  sub,
  dot,
  className,
}: {
  label: string;
  value: string;
  sub?: string;
  dot?: string;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-2.5 px-5 py-5 lg:px-6", className)}>
      <div className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        {dot && (
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: dot }} />
        )}
        <span className="truncate">{label}</span>
      </div>
      <div className="font-display text-[2.1rem] font-semibold leading-none tracking-tight tabular-nums">
        {value}
      </div>
      {sub && <div className="truncate text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="h-2 w-2 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}

function Dot() {
  return <span className="px-1.5 text-muted-foreground/40">·</span>;
}

function Skeleton({ className }: { className?: string }) {
  return <div className={cn("w-full animate-pulse rounded-lg bg-white/[0.04]", className)} />;
}

function plural(t: (k: string) => string, baseKey: string, n: number): string {
  const key = n === 1 ? `${baseKey}_one` : baseKey;
  return t(key).replace("{0}", n.toLocaleString());
}

function formatDay(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}
