import { useMemo } from "react";
import type { HeatmapCell } from "@/hooks/useBoard";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

const LEVEL: Record<number, string> = {
  0: "bg-white/[0.04] ring-1 ring-inset ring-white/[0.03]",
  1: "bg-primary/30",
  2: "bg-primary/50",
  3: "bg-primary/75",
  4: "bg-primary shadow-[0_0_8px_-1px] shadow-primary/50",
};

const DEFAULT_WEEKS = 26;

function isoOf(d: Date): string {
  return (
    `${d.getFullYear()}-` +
    `${String(d.getMonth() + 1).padStart(2, "0")}-` +
    `${String(d.getDate()).padStart(2, "0")}`
  );
}

interface Slot {
  date: string;
  events: number;
  hours: number;
  future: boolean;
}

/**
 * GitHub-style contribution grid: columns = weeks (oldest left, current week
 * right), rows = weekdays (Mon..Sun). Replaces the old boustrophedon "snake"
 * that was split into two scrolling halves. Bounded to ``weeks`` columns so it
 * fits a one-pager without horizontal scroll.
 */
export function ActivityHeatmap({
  cells,
  weeks = DEFAULT_WEEKS,
}: {
  cells: HeatmapCell[];
  weeks?: number;
}) {
  const t = useT();

  const { columns, monthLabels, max } = useMemo(() => {
    const byDate = new Map(cells.map((c) => [c.date, c]));
    const peak = Math.max(1, ...cells.map((c) => c.activity_events));
    const todayIso = cells.length
      ? cells[cells.length - 1].date
      : isoOf(new Date());
    const today = new Date(todayIso + "T00:00:00");
    // Monday-based weekday index: 0 = Mon … 6 = Sun.
    const weekday = (today.getDay() + 6) % 7;
    const thisMonday = new Date(today);
    thisMonday.setDate(today.getDate() - weekday);

    const cols: Slot[][] = [];
    const labels: string[] = [];
    let prevMonth = -1;
    for (let c = 0; c < weeks; c += 1) {
      const colMonday = new Date(thisMonday);
      colMonday.setDate(thisMonday.getDate() - (weeks - 1 - c) * 7);
      const month = colMonday.getMonth();
      // Label a column only when its first weekday rolls into a new month.
      if (month !== prevMonth) {
        labels.push(colMonday.toLocaleDateString(undefined, { month: "short" }));
        prevMonth = month;
      } else {
        labels.push("");
      }
      const col: Slot[] = [];
      for (let r = 0; r < 7; r += 1) {
        const d = new Date(colMonday);
        d.setDate(colMonday.getDate() + r);
        const iso = isoOf(d);
        const cell = byDate.get(iso);
        col.push({
          date: iso,
          events: cell?.activity_events ?? 0,
          hours: cell?.conversation_hours ?? 0,
          future: d > today,
        });
      }
      cols.push(col);
    }
    return { columns: cols, monthLabels: labels, max: peak };
  }, [cells, weeks]);

  return (
    <div className="inline-flex flex-col gap-1.5">
      <div className="flex gap-[3px]">
        {monthLabels.map((label, ci) => (
          <div
            key={ci}
            className="w-3.5 text-[9px] capitalize leading-none text-muted-foreground/70"
          >
            {label}
          </div>
        ))}
      </div>
      <div className="flex gap-[3px]">
        {columns.map((col, ci) => (
          <div key={ci} className="flex flex-col gap-[3px]">
            {col.map((slot, ri) => {
              if (slot.future) {
                return <div key={ri} className="h-3.5 w-3.5 rounded-[4px]" />;
              }
              const intensity =
                slot.events === 0
                  ? 0
                  : Math.min(4, Math.ceil((slot.events / max) * 4));
              return (
                <div
                  key={ri}
                  className={cn(
                    "h-3.5 w-3.5 rounded-[4px] transition-colors",
                    LEVEL[intensity],
                  )}
                  title={t("board_view.heatmap_tooltip")
                    .replace("{0}", slot.date)
                    .replace("{1}", String(slot.events))
                    .replace("{2}", slot.hours.toFixed(1))}
                />
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

export function HeatmapScale() {
  const t = useT();
  return (
    <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
      <span>{t("board_view.heatmap_less")}</span>
      {[0, 1, 2, 3, 4].map((lvl) => (
        <span key={lvl} className={cn("h-2.5 w-2.5 rounded-[2px]", LEVEL[lvl])} />
      ))}
      <span>{t("board_view.heatmap_more")}</span>
    </div>
  );
}
