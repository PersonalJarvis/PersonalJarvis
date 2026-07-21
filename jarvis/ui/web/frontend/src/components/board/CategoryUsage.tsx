import { useMemo } from "react";
import type { BoardCategories } from "@/hooks/useBoard";
import {
  BOARD_CATEGORY_KEYS,
  CATEGORY_META,
  categoryLabelKey,
  type BoardCategoryKey,
} from "@/lib/boardCategories";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * "What you use Jarvis for" — the honest analogue of a dictation tool's
 * "top apps" panel.
 * Every one of the six categories is shown (zeros muted at the bottom), each
 * with an icon tile, a smooth share bar, and a count + percentage indicator.
 */
export function CategoryUsage({ data }: { data: BoardCategories }) {
  const t = useT();

  const rows = useMemo(() => {
    const counts = new Map(data.categories.map((c) => [c.category, c.count]));
    const max = Math.max(1, ...data.categories.map((c) => c.count));
    return BOARD_CATEGORY_KEYS.map((key) => {
      const count = counts.get(key) ?? 0;
      return { key, count, frac: count / max };
    }).sort((a, b) => b.count - a.count);
  }, [data]);

  if (data.total === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        {t("board_view.categories_empty")}
      </p>
    );
  }

  return (
    <div className="flex flex-col justify-center gap-2.5">
      {rows.map(({ key, count, frac }) => {
        const meta = CATEGORY_META[key as BoardCategoryKey];
        const Icon = meta.icon;
        const pct = data.total ? Math.round((count / data.total) * 100) : 0;
        const empty = count === 0;
        return (
          <div
            key={key}
            className={cn("flex items-center gap-3", empty && "opacity-40")}
          >
            <div
              className={cn(
                "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg",
                "bg-white/[0.04] ring-1 ring-inset ring-white/[0.05]",
              )}
            >
              <Icon className={cn("h-4 w-4", meta.accent)} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex items-baseline justify-between gap-2">
                <span className="truncate text-xs font-medium text-foreground/90">
                  {t(categoryLabelKey(key))}
                </span>
                <span className="flex shrink-0 items-baseline gap-1">
                  <span className="font-display text-sm font-semibold tabular-nums">
                    {count.toLocaleString()}
                  </span>
                  <span className="text-[10px] text-muted-foreground tabular-nums">
                    {pct}%
                  </span>
                </span>
              </div>
              <div className="relative h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
                <div
                  className={cn(
                    "absolute inset-y-0 left-0 rounded-full transition-all duration-700",
                    meta.bar,
                    meta.glow,
                  )}
                  style={{ width: count > 0 ? `${Math.max(frac * 100, 5)}%` : "0%" }}
                />
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
