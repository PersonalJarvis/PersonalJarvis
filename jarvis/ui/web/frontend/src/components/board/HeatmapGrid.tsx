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
 * Aktivitaets-Heatmap im Snake-Layout (Boustrophedon).
 * Startet OBEN LINKS bei der ersten aktiven Zelle und laeuft abwechselnd
 * nach rechts und links durch die Zeilen nach unten:
 *   Reihe 1 (oben): links -> rechts
 *   Reihe 2:        rechts -> links
 *   Reihe 3:        links -> rechts
 *   ...
 * Fuehrende Null-Tage (heute/gestern ohne Aktivitaet) werden uebersprungen,
 * damit aktive Zellen am Anfang der Snake erscheinen.
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

    // Chronologisch absteigend: i=0 ist heute, i=total-1 ist aeltester Slot.
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

    // Fuehrende Null-Tage ueberspringen, damit die Snake bei der ersten
    // aktiven Zelle anfaengt statt beim heutigen leeren Tag.
    let startOffset = 0;
    while (
      startOffset < sequence.length &&
      sequence[startOffset].activity_events === 0
    ) {
      startOffset += 1;
    }
    if (startOffset >= total) startOffset = 0;

    // Boustrophedon von OBEN: k = Reihe von oben (k=0 oberste Zeile),
    // k=0 oben links->rechts, k=1 rechts->links, k=2 links->rechts, usw.
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
