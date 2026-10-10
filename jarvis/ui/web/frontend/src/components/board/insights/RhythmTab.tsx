import { useMemo } from "react";
import { Award, Bot, CalendarDays, Flame, type LucideIcon } from "lucide-react";

import type { BoardInsights } from "@/hooks/useBoardInsights";
import {
  formatDay,
  hourTotals,
  intensityLevels,
  milestoneProgress,
  peakSlot,
  plural,
  weekdayTotals,
  weeklyTotals,
  wordsOf,
  uiLocale,
} from "@/lib/boardInsights";
import { QuickTooltip } from "@/components/ui/tooltip";
import {
  CardTitleRow,
  HEAT_LEVEL_CLASS,
  InsightCard,
} from "@/components/board/insights/primitives";
import { useRunLocale } from "@/components/runs/format";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

const WEEKS_SHOWN = 26;

export function RhythmTab({ data }: { data: BoardInsights }) {
  return (
    <div className="flex flex-col gap-4">
      <PunchCard data={data} />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <WeeklyVolumeCard data={data} />
        <RecordsCard data={data} />
      </div>
    </div>
  );
}

function useWeekdayNames(style: "short" | "long") {
  const lang = useRunLocale();
  return useMemo(() => {
    const fmt = new Intl.DateTimeFormat(lang, { weekday: style });
    return Array.from({ length: 7 }, (_, i) => fmt.format(new Date(2024, 0, 1 + i)));
  }, [lang, style]);
}

function hourLabel(hour: number, lang: string): string {
  return new Date(2024, 0, 1, hour).toLocaleTimeString(lang, { hour: "numeric" });
}

function PunchCard({ data }: { data: BoardInsights }) {
  const t = useT();
  const lang = useRunLocale();
  const shortDays = useWeekdayNames("short");
  const longDays = useWeekdayNames("long");
  const punch = data.punch_card;
  const level = useMemo(() => intensityLevels(punch.flat()), [punch]);
  const peak = peakSlot(punch);
  const hours = hourTotals(punch);
  const maxHour = Math.max(1, ...hours);
  const weekdays = weekdayTotals(punch);
  const topDay = weekdays.indexOf(Math.max(...weekdays));

  return (
    <InsightCard testId="board-punch-card">
      <CardTitleRow
        title={t("board_insights.rhythm.title")}
        fact={
          peak
            ? t("board_insights.rhythm.peak")
                .replace("{0}", longDays[peak.weekday])
                .replace("{1}", hourLabel(peak.hour, lang))
            : undefined
        }
      >
        <p className="mt-1 text-sm text-muted-foreground">{t("board_insights.rhythm.subtitle")}</p>
      </CardTitleRow>
      {!peak ? (
        <p className="py-6 text-sm text-muted-foreground">{t("board_insights.rhythm.empty")}</p>
      ) : (
        <div className="overflow-x-auto">
          <div
            className="grid min-w-[640px] items-center gap-1"
            style={{ gridTemplateColumns: "3rem repeat(24, minmax(0, 1fr)) 4.5rem" }}
          >
            {punch.map((row, weekday) => (
              <PunchRow
                key={weekday}
                label={shortDays[weekday]}
                values={row}
                level={level}
                total={weekdays[weekday]}
                highlight={weekday === topDay}
                describe={(hour, value) =>
                  `${longDays[weekday]}, ${hourLabel(hour, lang)} — ${value.toLocaleString(uiLocale())}`
                }
              />
            ))}
            <span />
            {hours.map((value, hour) => (
              <div key={hour} className="flex h-12 items-end" aria-hidden>
                <div
                  className="w-full rounded-t-[3px] bg-accent/60"
                  style={{ height: `${Math.max((value / maxHour) * 100, value > 0 ? 4 : 0)}%` }}
                />
              </div>
            ))}
            <span />
            <span />
            {hours.map((_, hour) => (
              <span key={hour} className="text-center text-xs tabular-nums text-muted-foreground">
                {hour % 3 === 0 ? String(hour).padStart(2, "0") : ""}
              </span>
            ))}
            <span />
          </div>
        </div>
      )}
    </InsightCard>
  );
}

function PunchRow({
  label,
  values,
  level,
  total,
  highlight,
  describe,
}: {
  label: string;
  values: number[];
  level: (v: number) => 0 | 1 | 2 | 3 | 4;
  total: number;
  highlight: boolean;
  describe: (hour: number, value: number) => string;
}) {
  return (
    <>
      <span className={cn("text-xs", highlight ? "font-semibold text-foreground-strong" : "text-muted-foreground")}>
        {label}
      </span>
      {values.map((value, hour) => (
        <QuickTooltip key={hour} content={describe(hour, value)} side="top" className="block">
          <span className={cn("block h-6 w-full rounded-[4px]", HEAT_LEVEL_CLASS[level(value)])} />
        </QuickTooltip>
      ))}
      <span className="text-right text-xs tabular-nums text-muted-foreground">{total.toLocaleString(uiLocale())}</span>
    </>
  );
}

function WeeklyVolumeCard({ data }: { data: BoardInsights }) {
  const t = useT();
  const lang = useRunLocale();
  const weeks = weeklyTotals(data.days, WEEKS_SHOWN, 8);
  const max = Math.max(1, ...weeks.map((w) => w.words));
  const best = weeks.reduce((b, w) => (w.words > b ? w.words : b), 0);
  const monthFmt = new Intl.DateTimeFormat(lang, { month: "short" });

  return (
    <InsightCard testId="board-weekly-volume">
      <CardTitleRow
        title={t("board_insights.weekly.title")}
        fact={best > 0 ? t("board_insights.weekly.best").replace("{0}", best.toLocaleString(uiLocale())) : undefined}
      >
        <p className="mt-1 text-sm text-muted-foreground">{t("board_insights.weekly.subtitle")}</p>
      </CardTitleRow>
      <div className="flex h-44 items-end gap-1">
        {weeks.map((week, i) => {
          const date = week.start ? new Date(`${week.start}T00:00:00`) : null;
          const tip = `${week.start ? formatDay(week.start, lang) : ""} — ${t("board_insights.weekly.tooltip")
            .replace("{0}", week.words.toLocaleString(uiLocale()))
            .replace("{1}", week.agentSessions.toLocaleString(uiLocale()))}`;
          return (
            <QuickTooltip key={i} content={tip} side="top" className="flex h-full flex-1 flex-col justify-end">
              <div
                className={cn(
                  "w-full rounded-t-[4px]",
                  i === weeks.length - 1 ? "bg-accent/45" : "bg-accent",
                )}
                style={{ height: `${Math.max((week.words / max) * 100, week.words > 0 ? 2 : 0)}%` }}
              />
              <span className="mt-1.5 h-4 text-center text-xs text-muted-foreground">
                {date && date.getDate() <= 7 ? monthFmt.format(date) : ""}
              </span>
            </QuickTooltip>
          );
        })}
      </div>
    </InsightCard>
  );
}

function RecordsCard({ data }: { data: BoardInsights }) {
  const t = useT();
  const lang = useRunLocale();
  const totalWords = data.dictation.words + data.voice.user_words;
  const { previous, next } = milestoneProgress(totalWords);
  const progress = next > previous ? (totalWords - previous) / (next - previous) : 0;
  const bestWords = data.records.best_words_day;
  const bestAgents = data.records.best_agent_day;
  const todayWords = data.days.length ? wordsOf(data.days[data.days.length - 1]) : 0;

  return (
    <InsightCard testId="board-records">
      <CardTitleRow title={t("board_insights.records.title")} />
      <ul className="flex flex-col divide-y divide-border">
        <RecordRow
          icon={Award}
          label={t("board_insights.records.best_words")}
          value={bestWords ? bestWords.value.toLocaleString(uiLocale()) : "—"}
          sub={bestWords ? formatDay(bestWords.date, lang) : undefined}
        />
        <RecordRow
          icon={Bot}
          label={t("board_insights.records.best_agents")}
          value={bestAgents ? bestAgents.value.toLocaleString(uiLocale()) : "—"}
          sub={bestAgents ? formatDay(bestAgents.date, lang) : undefined}
        />
        <RecordRow
          icon={Flame}
          label={t("board_insights.records.longest_streak")}
          value={plural(t, "board_insights.records.days", data.streak.longest_days)}
        />
        <RecordRow
          icon={CalendarDays}
          label={t("board_insights.records.active_days")}
          value={data.streak.active_days.toLocaleString(uiLocale())}
          sub={
            data.streak.first_day
              ? t("board_insights.records.since").replace("{0}", formatDay(data.streak.first_day, lang))
              : undefined
          }
        />
      </ul>
      <div className="mt-auto pt-5">
        <div className="mb-2 flex items-baseline justify-between gap-3 text-sm">
          <span className="font-medium text-foreground-strong">
            {t("board_insights.records.next_milestone").replace("{0}", next.toLocaleString(uiLocale()))}
          </span>
          <span className="tabular-nums text-muted-foreground">
            {t("board_insights.records.to_go").replace("{0}", Math.max(0, next - totalWords).toLocaleString(uiLocale()))}
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-sheen/[0.06]">
          <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(100, progress * 100)}%` }} />
        </div>
        {todayWords > 0 && (
          <p className="mt-2 text-xs text-muted-foreground">
            {t("board_insights.records.today").replace("{0}", todayWords.toLocaleString(uiLocale()))}
          </p>
        )}
      </div>
    </InsightCard>
  );
}

function RecordRow({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <li className="flex items-center gap-3 py-2.5">
      <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-foreground">{label}</div>
        {sub && <div className="truncate text-xs text-muted-foreground">{sub}</div>}
      </div>
      <span className="shrink-0 text-base font-semibold tabular-nums text-foreground-strong">{value}</span>
    </li>
  );
}
