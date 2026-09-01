import { cn } from "@/lib/utils";
import type { RunListItem } from "./types";
import { OutcomeDot } from "./OutcomeBadge";
import { FeatureBadges } from "./FeatureBadges";

/**
 * The run rail. One row per recorded session.
 *
 * Separation is fill, not rule: the row rests on the rail's own ground, hover
 * lifts it to --secondary and selection keeps that lift with the label in the
 * ink ceiling. The old row drew a border AND a 2px left tick AND a background
 * — three devices saying the same thing, of which the fill is the only one an
 * eye actually reads on near-black.
 */
export function RunList({
  items,
  selectedId,
  onSelect,
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
                "flex w-full flex-col gap-1.5 rounded-md px-3 py-2.5 text-left transition-colors",
                selected
                  ? "bg-secondary text-foreground-strong"
                  : "hover:bg-secondary",
              )}
            >
              <div className="flex items-center gap-2">
                <OutcomeDot outcome={r.outcome} />
                <span className="flex-1 truncate text-body">
                  {r.preview || r.session_id.slice(0, 8)}
                </span>
                <span className="shrink-0 text-micro tabular-nums text-muted-foreground">
                  {new Date(r.started_ms).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </div>
              <div className="flex items-center gap-1.5 pl-4 text-micro tabular-nums text-muted-foreground">
                <span>{r.turn_count} turns</span>
                {r.duration_s !== null && <span>· {r.duration_s.toFixed(1)}s</span>}
                {slow && <span className="text-warning">· slow</span>}
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
