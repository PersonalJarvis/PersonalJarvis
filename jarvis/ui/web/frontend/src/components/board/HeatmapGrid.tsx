import { useMemo } from "react";
import type { HeatmapCell } from "@/hooks/useBoard";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

interface HeatmapGridProps {
  cells: HeatmapCell[];
  weeks?: number;
  showLegend?: boolean;
}

const ROWS = 7;

/**
 * Activity heatmap in a snake layout (boustrophedon).
 * Starts TOP LEFT at the first active cell and alternates
 * right and left through the rows going down:
 *   row 1 (top):    left -> right
 *   row 2:           right -> left
 *   row 3:           left -> right
 *   ...
 * Leading zero-activity days (today/yesterday with no activity) are skipped,
 * so active cells appear at the start of the snake.
 */
export function HeatmapGrid({ cells, weeks = 53, showLegend = true }: HeatmapGridProps) {
  const t = useT();
  const cols = weeks;

  const byDate = useMemo(() => {
    const map = new Map<string, HeatmapCell>();
    for (const c of cells) map.set(c.date, c);
    return map;
  }, [cells]);

  const grid = useMemo(() => {
    if (cells.length === 0) return [] as HeatmapCell[][];
    const newest = new Date(cells[cells.length - 1].date + "T00:00:00");
    const total = ROWS * cols;

    // Chronologically descending: i=0 is today, i=total-1 is the oldest slot.
    const sequence: HeatmapCell[] = new Array(total);
    for (let i = 0; i < total; i += 1) {
      const day = new Date(newest);
      day.setDate(newest.getDate() - i);
      const iso = day.toISOString().slice(0, 10);
      sequence[i] = byDate.get(iso) ?? {
        date: iso,
        tasks_completed: 0,
        tasks_failed: 0,
        activity_events: 0,
        conversation_hours: 0,
        user_words: 0,
        jarvis_words: 0,
      };
    }

    // Skip leading zero-activity days so the snake starts at the first
    // active cell instead of at today's empty day.
    let startOffset = 0;
    while (
      startOffset < sequence.length &&
      sequence[startOffset].activity_events === 0
    ) {
      startOffset += 1;
    }
    if (startOffset >= total) startOffset = 0;

    // Boustrophedon from the TOP: k = row from the top (k=0 topmost row),
    // k=0 top left->right, k=1 right->left, k=2 left->right, etc.
    const layout: HeatmapCell[][] = Array.from(
      { length: ROWS },
      () => new Array<HeatmapCell>(cols),
    );
    for (let i = 0; i < total; i += 1) {
      const sourceIdx = i + startOffset;
      if (sourceIdx >= sequence.length) break;
      const k = Math.floor(i / cols);
      if (k >= ROWS) break;
      const offset = i % cols;
      const cssRow = k;
      const col = k % 2 === 0 ? offset : cols - 1 - offset;
      layout[cssRow][col] = sequence[sourceIdx];
    }
    return layout;
  }, [cells, byDate, cols]);

  const max = useMemo(
    () => Math.max(1, ...cells.map((c) => c.activity_events)),
    [cells],
  );

  return (
    <div className="overflow-x-auto">
      <div
        className="grid gap-[3px]"
        style={{
          gridTemplateColumns: `repeat(${cols}, 14px)`,
          gridTemplateRows: `repeat(${ROWS}, 14px)`,
        }}
      >
        {grid.flatMap((row, r) =>
          row.map((c, ci) => {
            if (!c) {
              return (
                <div
                  key={`${r}-${ci}`}
                  className="h-[14px] w-[14px] rounded-[3px] border border-border/20 bg-muted/10"
                />
              );
            }
            const intensity = c.activity_events === 0
              ? 0
              : Math.min(4, Math.ceil((c.activity_events / max) * 4));
            return (
              <div
                key={`${r}-${ci}`}
                className={cn(
                  "h-[14px] w-[14px] rounded-[3px] border border-border/40",
                  LEVEL[intensity],
                )}
                title={t("board.heatmap_tooltip").replace("{0}", c.date).replace("{1}", String(c.activity_events)).replace("{2}", String(c.tasks_completed)).replace("{3}", c.conversation_hours.toFixed(1))}
              />
            );
          }),
        )}
      </div>
      {showLegend && <HeatmapLegend className="mt-3" />}
    </div>
  );
}

export function HeatmapLegend({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground",
        className,
      )}
    >
      <span>less</span>
      {[0, 1, 2, 3, 4].map((lvl) => (
        <span
          key={lvl}
          className={cn("h-[10px] w-[10px] rounded-[2px]", LEVEL[lvl])}
        />
      ))}
      <span>more</span>
    </div>
  );
}

const LEVEL: Record<number, string> = {
  0: "bg-muted/20",
  1: "bg-primary/25",
  2: "bg-primary/45",
  3: "bg-primary/70",
  4: "bg-primary",
};
