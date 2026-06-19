import { cn } from "@/lib/utils";
import type { RunListItem } from "./types";
import { OutcomeDot } from "./OutcomeBadge";
import { FeatureBadges } from "./FeatureBadges";

export function RunList({
  items, selectedId, onSelect,
}: {
  items: RunListItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <ul className="space-y-1 p-2" data-testid="run-list">
      {items.map((r) => {
        const selected = r.session_id === selectedId;
        const slow = r.slo_status === "breach" || r.slo_status === "warn";
        return (
          <li key={r.session_id}>
            <button
              type="button"
              onClick={() => onSelect(r.session_id)}
              className={cn(
                "group relative flex w-full flex-col gap-1.5 rounded-lg border px-3 py-2.5 text-left transition-colors",
                selected
                  ? "border-border bg-background"
                  : "border-transparent hover:border-border/60 hover:bg-background/50",
              )}
            >
              {selected && (
                <span className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-primary" />
              )}
              <div className="flex items-center gap-2">
                <OutcomeDot outcome={r.outcome} />
                <span className="flex-1 truncate text-sm">
                  {r.preview || r.session_id.slice(0, 8)}
                </span>
                <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
                  {new Date(r.started_ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>
              <div className="flex items-center gap-1.5 pl-4 text-[10px] tabular-nums text-muted-foreground">
                <span>{r.turn_count} turns</span>
                {r.duration_s !== null && <span>· {r.duration_s.toFixed(1)}s</span>}
                {slow && <span className="text-amber-400/80">· slow</span>}
              </div>
              {r.feature_tags.length > 0 && (
                <div className="pl-4">
                  <FeatureBadges tags={r.feature_tags} max={3} size="xs" />
                </div>
              )}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
