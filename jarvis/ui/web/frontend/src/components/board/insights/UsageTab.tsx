import { Bot, Mic, MessagesSquare, PhoneCall, TrendingDown, TrendingUp, type LucideIcon } from "lucide-react";

import type { BoardInsights } from "@/hooks/useBoardInsights";
import {
  minutesSaved,
  plural,
  trendPercent,
  usageMix,
  wordsPerMinute,
  type MixRow,
  uiLocale,
} from "@/lib/boardInsights";
import {
  BigNumber,
  CardTitleRow,
  Eyebrow,
  Hairline,
  InsightCard,
} from "@/components/board/insights/primitives";
import { QuickTooltip } from "@/components/ui/tooltip";
import { SpeedGauge } from "@/components/board/insights/SpeedGauge";
import { StreakCalendar } from "@/components/board/insights/StreakCalendar";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

export function UsageTab({ data }: { data: BoardInsights }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <SpeedCard data={data} />
        <TimeSavedCard data={data} />
        <WordsCard data={data} />
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <UsageMixCard data={data} />
        <StreakCard data={data} />
      </div>
    </div>
  );
}

function SpeedCard({ data }: { data: BoardInsights }) {
  const t = useT();
  const typing = data.reference.typing_wpm;
  const wpm = wordsPerMinute(data.dictation.words, data.dictation.seconds);
  const ratio = typing > 0 ? wpm / typing : 0;
  return (
    <InsightCard testId="board-speed-card">
      <BigNumber>{wpm > 0 ? Math.round(wpm).toLocaleString(uiLocale()) : "—"}</BigNumber>
      <div className="mt-2">
        <Eyebrow hint={t("board_insights.speed.hint").replace("{0}", String(typing))}>
          {t("board_insights.speed.label")}
        </Eyebrow>
      </div>
      <div className="mt-auto flex flex-col items-center pt-4">
        {wpm > 0 ? (
          <>
            <SpeedGauge
              wpm={wpm}
              typingWpm={typing}
              centerTop={`${ratio.toFixed(1)}×`}
              centerBottom={t("board_insights.speed.vs_typing")}
            />
            <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
              <span aria-hidden className="h-3 w-px bg-foreground/60" />
              {t("board_insights.speed.typing_mark").replace("{0}", String(typing))}
            </p>
          </>
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {t("board_insights.speed.empty")}
          </p>
        )}
      </div>
    </InsightCard>
  );
}

function TimeSavedCard({ data }: { data: BoardInsights }) {
  const t = useT();
  const typing = data.reference.typing_wpm;
  const minutes = minutesSaved(data.dictation.words, data.dictation.seconds, typing);
  const hours = minutes / 60;
  const spokenHours = data.dictation.seconds / 3600;
  return (
    <InsightCard testId="board-time-saved-card">
      <BigNumber>
        {data.dictation.words > 0
          ? hours >= 10
            ? `${Math.round(hours).toLocaleString(uiLocale())} h`
            : `${hours.toFixed(1)} h`
          : "—"}
      </BigNumber>
      <div className="mt-2">
        <Eyebrow hint={t("board_insights.saved.hint").replace("{0}", String(typing))}>
          {t("board_insights.saved.label")}
        </Eyebrow>
      </div>
      <Hairline />
      <dl className="flex flex-col gap-3 text-base">
        <FactRow
          value={data.dictation.dictations.toLocaleString(uiLocale())}
          label={t("board_insights.saved.dictations")}
        />
        <FactRow
          value={`${spokenHours >= 10 ? Math.round(spokenHours) : spokenHours.toFixed(1)} h`}
          label={t("board_insights.saved.spoken")}
        />
        <FactRow
          value={`${Math.round(typing > 0 ? data.dictation.words / typing / 60 : 0).toLocaleString(uiLocale())} h`}
          label={t("board_insights.saved.typing_would_take")}
        />
      </dl>
    </InsightCard>
  );
}

function FactRow({ value, label }: { value: string; label: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="truncate text-muted-foreground">{label}</dt>
      <dd className="shrink-0 font-medium tabular-nums text-foreground-strong">{value}</dd>
    </div>
  );
}

function WordsCard({ data }: { data: BoardInsights }) {
  const t = useT();
  const dictated = data.dictation.words;
  const spoken = data.voice.user_words;
  const total = dictated + spoken;
  const novels = data.reference.novel_words > 0 ? total / data.reference.novel_words : 0;
  const trend = trendPercent(data.trend.words_30d, data.trend.words_prev_30d);
  const dictatedShare = total > 0 ? dictated / total : 0;

  return (
    <InsightCard testId="board-words-card">
      <div className="flex items-start justify-between gap-3">
        <BigNumber>{total.toLocaleString(uiLocale())}</BigNumber>
        {trend !== null && (
          <QuickTooltip content={t("board_insights.words.trend_hint")} side="top" className="shrink-0">
            <span className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-xs font-medium tabular-nums text-foreground">
              {trend >= 0 ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
              {t("board_insights.words.trend").replace("{0}", `${trend > 0 ? "+" : ""}${trend}`)}
            </span>
          </QuickTooltip>
        )}
      </div>
      <div className="mt-2">
        <Eyebrow hint={t("board_insights.words.novel_hint").replace("{0}", data.reference.novel_words.toLocaleString(uiLocale()))}>
          {t("board_insights.words.label")}
        </Eyebrow>
      </div>
      <Hairline />
      <p className="text-base text-foreground">
        {total === 0
          ? t("board_insights.words.empty")
          : novels >= 1
            ? t("board_insights.words.novels").replace("{0}", novels.toFixed(1))
            : t("board_insights.words.novel_share").replace("{0}", String(Math.max(1, Math.round(novels * 100))))}
      </p>
      {total > 0 && (
        <div className="mt-auto pt-4">
          <div className="flex h-7 gap-1 overflow-hidden rounded-md text-xs font-medium">
            {dictated > 0 && (
              <SplitSegment
                icon={Mic}
                label={t("board_insights.words.dictated")}
                share={dictatedShare}
                className="bg-accent text-accent-foreground"
              />
            )}
            {spoken > 0 && (
              <SplitSegment
                icon={PhoneCall}
                label={t("board_insights.words.spoken")}
                share={1 - dictatedShare}
                className="bg-accent/30 text-foreground"
              />
            )}
          </div>
          <div className="mt-2 flex justify-between text-xs tabular-nums text-muted-foreground">
            <span>{dictated.toLocaleString(uiLocale())}</span>
            <span>{spoken.toLocaleString(uiLocale())}</span>
          </div>
        </div>
      )}
    </InsightCard>
  );
}

function SplitSegment({
  icon: Icon,
  label,
  share,
  className,
}: {
  icon: LucideIcon;
  label: string;
  share: number;
  className: string;
}) {
  // A sliver still gets room for its icon, so a small share stays legible.
  return (
    <div
      className={cn("flex min-w-[28px] items-center gap-1.5 overflow-hidden rounded-md px-2", className)}
      style={{ flexGrow: share, flexBasis: 0 }}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" aria-label={label} />
      {share >= 0.15 && <span className="truncate">{label}</span>}
    </div>
  );
}

const MIX_ICON: Record<MixRow["key"], LucideIcon> = {
  dictations: Mic,
  agent_sessions: Bot,
  chat_messages: MessagesSquare,
  voice_sessions: PhoneCall,
};

function UsageMixCard({ data }: { data: BoardInsights }) {
  const t = useT();
  const rows = usageMix(data);
  const total = rows.reduce((s, r) => s + r.count, 0);
  return (
    <InsightCard testId="board-usage-mix">
      <CardTitleRow
        title={t("board_insights.mix.title")}
        fact={t("board_insights.mix.active_days").replace("{0}", data.streak.active_days.toLocaleString(uiLocale()))}
      />
      {total === 0 ? (
        <p className="text-sm text-muted-foreground">{t("board_insights.mix.empty")}</p>
      ) : (
        <ul className="flex flex-1 flex-col justify-center gap-3">
          {rows.map((row) => {
            const Icon = MIX_ICON[row.key];
            const pct = Math.round(row.share * 100);
            return (
              <li key={row.key} className={cn("flex items-center gap-3", row.count === 0 && "opacity-50")}>
                <Icon className="h-5 w-5 shrink-0 text-muted-foreground" aria-hidden />
                <div className="relative h-9 w-[38%] shrink-0 overflow-hidden rounded-md bg-sheen/[0.05]">
                  <div
                    className="absolute inset-y-0 left-0 rounded-md bg-accent"
                    // Wide enough to carry its own percentage, however small the share.
                    style={{ width: row.count > 0 ? `${Math.max(row.share * 100, 18)}%` : "0%" }}
                  />
                  <span
                    className={cn(
                      "absolute inset-y-0 left-2.5 flex items-center text-sm font-semibold tabular-nums",
                      row.count > 0 ? "text-accent-foreground" : "text-muted-foreground",
                    )}
                  >
                    {pct < 1 && row.count > 0 ? "<1%" : `${pct}%`}
                  </span>
                </div>
                <span className="min-w-0 truncate text-sm font-medium uppercase tracking-[0.06em] text-foreground">
                  {plural(t, `board_insights.mix.${row.key}`, row.count)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </InsightCard>
  );
}

function StreakCard({ data }: { data: BoardInsights }) {
  const t = useT();
  const current = data.streak.current_days;
  return (
    <InsightCard testId="board-streak-card">
      <CardTitleRow
        title={current > 0 ? plural(t, "board_insights.streak.title", current) : t("board_insights.streak.none")}
        fact={plural(t, "board_insights.streak.longest", data.streak.longest_days)}
      />
      <StreakCalendar days={data.days} />
    </InsightCard>
  );
}
