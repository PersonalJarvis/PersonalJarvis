import {
  Award,
  CheckCircle2,
  Loader2,
  MessageCircle,
  Mic,
  RefreshCw,
  Sparkles,
  Wrench,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { StatsCard } from "@/components/board/StatsCard";
import { HeatmapGrid, HeatmapLegend } from "@/components/board/HeatmapGrid";
import { ToolBarChart } from "@/components/board/ToolBarChart";
import { PersonalRecordsList } from "@/components/board/PersonalRecordsList";
import { AIProfileCard } from "@/components/board/AIProfileCard";
import { AchievementGrid } from "@/components/board/AchievementGrid";
import {
  useBoardHeatmap,
  useBoardRecords,
  useBoardRefresh,
  useBoardSummary,
  useBoardTools,
} from "@/hooks/useBoard";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

export function BoardView() {
  const t = useT();
  const summary = useBoardSummary();
  const heatmap = useBoardHeatmap(365);
  const tools = useBoardTools(90);
  const records = useBoardRecords();
  const refresh = useBoardRefresh();

  const s = summary.data;

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Sparkles className="h-4 w-4 text-primary" />}
        title={t("board_view.title")}
        subtitle={t("board_view.subtitle")}
        right={
          <button
            type="button"
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending}
            className={cn(
              "inline-flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-xs font-medium transition-colors",
              "hover:border-primary/40 hover:bg-background/60",
              refresh.isPending && "opacity-60",
            )}
            title={t("board_view.refresh_tooltip")}
          >
            <RefreshCw
              className={cn(
                "h-3.5 w-3.5",
                refresh.isPending && "animate-spin",
              )}
            />
            {t("board_view.refresh")}
          </button>
        }
      />

      <div className="flex-1 space-y-6 overflow-y-auto scrollbar-jarvis p-6">
        {summary.isLoading && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> {t("board_view.loading")}
          </div>
        )}
        {summary.error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
            {t("board_view.load_error")}: {String((summary.error as Error).message)}
          </div>
        )}

        {/* Stats-Cards */}
        <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          <StatsCard
            icon={<CheckCircle2 className="h-3.5 w-3.5" />}
            label={t("board_view.stats.tasks_completed")}
            value={s?.totals.tasks_completed ?? "—"}
            sublabel={
              s
                ? t("board_view.stats.tasks_completed_sub").replace("{0}", String(s.totals.active_days))
                : undefined
            }
          />
          <StatsCard
            icon={<Wrench className="h-3.5 w-3.5" />}
            label={t("board_view.stats.tools_in_window").replace("{0}", String(s?.window_days ?? 30))}
            value={s?.window.unique_tools ?? "—"}
            sublabel={t("board_view.stats.tools_in_window_sub")}
          />
          <StatsCard
            icon={<Mic className="h-3.5 w-3.5" />}
            label={t("board_view.stats.voice_first_try")}
            value={formatRate(s?.window.voice_first_try_rate)}
            sublabel={
              s?.window.voice_commands
                ? t("board_view.stats.voice_first_try_sub").replace("{0}", String(s.window.voice_commands))
                : t("board_view.stats.voice_no_data")
            }
            tone={
              s?.window.voice_first_try_rate !== undefined &&
              s?.window.voice_first_try_rate !== null
                ? s.window.voice_first_try_rate >= 0.95
                  ? "success"
                  : undefined
                : undefined
            }
          />
          <StatsCard
            icon={<Award className="h-3.5 w-3.5" />}
            label={t("board_view.stats.hours_saved")}
            value={(s?.totals.hours_saved ?? 0).toFixed(1)}
            sublabel={t("board_view.stats.hours_saved_sub")}
          />
          <StatsCard
            icon={<MessageCircle className="h-3.5 w-3.5" />}
            label={t("board_view.stats.conversation_hours")}
            value={(s?.totals.conversation_hours ?? 0).toFixed(1)}
            sublabel={
              s
                ? t("board_view.stats.conversation_hours_sub").replace("{0}", s.window.conversation_hours.toFixed(1)).replace("{1}", String(s.window_days))
                : undefined
            }
          />
        </section>

        {/* Heatmap — beide Halbjahre parallel in einer Card */}
        <section className="space-y-4 rounded-xl border border-border bg-card/30 p-5 backdrop-blur">
          <div className="flex items-end justify-between">
            <div>
              <h3 className="font-display text-sm font-semibold">{t("board_view.activity_title")}</h3>
              <p className="text-xs text-muted-foreground">
                {t("board_view.activity_description")}
              </p>
            </div>
            {s?.streak_days ? (
              <span className="rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                {s.streak_days}-Tage-Serie
              </span>
            ) : null}
          </div>
          {heatmap.data ? (
            <>
              <div className="grid gap-6 lg:grid-cols-2">
                <div className="space-y-2">
                  <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    Letzte 26 Wochen
                  </p>
                  <HeatmapGrid
                    cells={heatmap.data.cells.slice(-183)}
                    weeks={26}
                    showLegend={false}
                  />
                </div>
                <div className="space-y-2">
                  <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    Wochen 27 bis 52
                  </p>
                  <HeatmapGrid
                    cells={heatmap.data.cells.slice(0, 182)}
                    weeks={26}
                    showLegend={false}
                  />
                </div>
              </div>
              <HeatmapLegend />
            </>
          ) : (
            <div className="h-24 animate-pulse rounded-md bg-muted/10" />
          )}
        </section>

        {/* AI-Profile */}
        <AIProfileCard />

        {/* Achievements */}
        <AchievementGrid />

        {/* Tools + Records */}
        <div className="grid gap-6 xl:grid-cols-[2fr_1fr]">
          <section className="space-y-3 rounded-xl border border-border bg-card/30 p-5 backdrop-blur">
            <div>
              <h3 className="font-display text-sm font-semibold">Tool-Nutzung</h3>
              <p className="text-xs text-muted-foreground">
                Tage, an denen ein Tool mindestens einmal erfolgreich lief. Fenster: 90 Tage.
              </p>
            </div>
            {tools.data ? (
              <ToolBarChart histogram={tools.data.histogram} />
            ) : (
              <div className="h-48 animate-pulse rounded-md bg-muted/10" />
            )}
          </section>

          <section className="space-y-3 rounded-xl border border-border bg-card/30 p-5 backdrop-blur">
            <div>
              <h3 className="font-display text-sm font-semibold">Personal Records</h3>
              <p className="text-xs text-muted-foreground">
                Deine persoenlichen Bestwerte. Nur du siehst sie.
              </p>
            </div>
            {records.data ? (
              <PersonalRecordsList records={records.data.records} />
            ) : (
              <div className="h-24 animate-pulse rounded-md bg-muted/10" />
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

function formatRate(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return "—";
  return `${Math.round(rate * 100)} %`;
}
