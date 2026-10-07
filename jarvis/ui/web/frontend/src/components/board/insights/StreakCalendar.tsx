import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import type { InsightsDay } from "@/hooks/useBoardInsights";
import { actionsOf, calendarWeeks, intensityLevels, wordsOf } from "@/lib/boardInsights";
import { QuickTooltip } from "@/components/ui/tooltip";
import { HEAT_LEVEL_CLASS } from "@/components/board/insights/primitives";
import { useT, useUiLanguage } from "@/i18n";
import { cn } from "@/lib/utils";

/** Weeks shown at once; the arrows page through the rest of the year. */
const PAGE_WEEKS = 22;

/**
 * Weekday rows x week columns, one square per day, shaded by how many things
 * the user started that day. Pages back through the 53-week series.
 */
export function StreakCalendar({ days }: { days: readonly InsightsDay[] }) {
  const t = useT();
  const lang = useUiLanguage();
  const weeks = useMemo(() => calendarWeeks(days), [days]);
  const level = useMemo(() => intensityLevels(days.map(actionsOf)), [days]);
  const lastStart = Math.max(0, weeks.length - PAGE_WEEKS);
  const [start, setStart] = useState(lastStart);
  const page = weeks.slice(start, start + PAGE_WEEKS);
  const today = days[days.length - 1]?.date;

  const monthName = useMemo(() => {
    const fmt = new Intl.DateTimeFormat(lang, { month: "short" });
    return (m: number) => fmt.format(new Date(2024, m, 1));
  }, [lang]);
  const weekdayNames = useMemo(() => {
    const fmt = new Intl.DateTimeFormat(lang, { weekday: "short" });
    // 2024-01-01 was a Monday.
    return Array.from({ length: 7 }, (_, i) => fmt.format(new Date(2024, 0, 1 + i)));
  }, [lang]);
  const dayLabel = useMemo(() => {
    const fmt = new Intl.DateTimeFormat(lang, { weekday: "short", day: "numeric", month: "short" });
    return (iso: string) => fmt.format(new Date(`${iso}T00:00:00`));
  }, [lang]);

  const describe = (day: InsightsDay) => {
    const actions = actionsOf(day);
    if (actions === 0 && day.agent_turns === 0) {
      return `${dayLabel(day.date)} — ${t("board_insights.calendar.nothing")}`;
    }
    const parts = [
      t("board_insights.calendar.started").replace("{0}", actions.toLocaleString()),
    ];
    const words = wordsOf(day);
    if (words > 0) {
      parts.push(t("board_insights.calendar.words").replace("{0}", words.toLocaleString()));
    }
    return `${dayLabel(day.date)} — ${parts.join(" · ")}`;
  };

  const navButton =
    "flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sheen/[0.06] hover:text-foreground disabled:pointer-events-none disabled:opacity-30";

  return (
    <div className="flex min-w-0 flex-col gap-3" data-testid="board-streak-calendar">
      <div className="grid items-center gap-1" style={{ gridTemplateColumns: `2.25rem repeat(${PAGE_WEEKS}, minmax(0, 1fr))` }}>
        <button
          type="button"
          className={navButton}
          onClick={() => setStart((s) => Math.max(0, s - PAGE_WEEKS))}
          disabled={start === 0}
          aria-label={t("board_insights.calendar.older")}
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        {page.map((week, i) => (
          <div key={i} className="relative h-4 text-xs text-muted-foreground">
            {week.monthStart !== null && (
              <span className="absolute left-0 top-0 whitespace-nowrap">{monthName(week.monthStart)}</span>
            )}
          </div>
        ))}
        {Array.from({ length: 7 }, (_, row) => (
          <Row
            key={row}
            label={row % 2 === 0 ? weekdayNames[row] : ""}
            cells={page.map((week) => week.days[row])}
            level={level}
            describe={describe}
            today={today}
          />
        ))}
      </div>
      <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
        <div className="flex items-center gap-1.5">
          <span>{t("board_insights.calendar.less")}</span>
          {([0, 1, 2, 3, 4] as const).map((l) => (
            <span key={l} className={cn("h-3 w-3 rounded-[3px]", HEAT_LEVEL_CLASS[l])} />
          ))}
          <span>{t("board_insights.calendar.more")}</span>
        </div>
        <button
          type="button"
          className={navButton}
          onClick={() => setStart((s) => Math.min(lastStart, s + PAGE_WEEKS))}
          disabled={start >= lastStart}
          aria-label={t("board_insights.calendar.newer")}
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

function Row({
  label,
  cells,
  level,
  describe,
  today,
}: {
  label: string;
  cells: (InsightsDay | null)[];
  level: (v: number) => 0 | 1 | 2 | 3 | 4;
  describe: (day: InsightsDay) => string;
  today: string | undefined;
}) {
  return (
    <>
      <span className="truncate pr-1 text-xs text-muted-foreground">{label}</span>
      {cells.map((day, i) =>
        day ? (
          <QuickTooltip key={i} content={describe(day)} side="top" className="block">
            <span
              className={cn(
                "block aspect-square w-full max-w-[22px] rounded-[4px]",
                HEAT_LEVEL_CLASS[level(actionsOf(day))],
                day.date === today && "ring-1 ring-foreground/50 ring-offset-1 ring-offset-card",
              )}
            />
          </QuickTooltip>
        ) : (
          <span key={i} className="block aspect-square w-full max-w-[22px]" />
        ),
      )}
    </>
  );
}
