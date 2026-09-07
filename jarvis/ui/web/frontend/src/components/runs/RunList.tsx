import { cn } from "@/lib/utils";

import { FeatureBadges } from "./FeatureBadges";
import { OutcomeDot } from "./OutcomeBadge";
import type { RunListItem } from "./types";

/**
 * The run rail. One row per recorded session.
 *
 * Separation is fill, not rule: the row rests on the rail's own ground, hover
 * lifts it to --secondary and selection keeps that lift plus the shared
 * `jarvis-nav-active` tick, which is the one selected-row treatment every rail
 * in the app wears. The outcome is a dot, latency and faults are words in
 * their status ink — a run that is merely slow must never read like one that
 * failed.
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
    <ul className="space-y-0.5" data-testid="run-list">
      {items.map((r) => {
        const selected = r.session_id === selectedId;
        const slow = r.slo_status === "breach" || r.slo_status === "warn";
        return (
          <li key={r.session_id}>
            <button
              type="button"
              onClick={() => onSelect(r.session_id)}
              aria-current={selected ? "true" : undefined}
              className={cn(
                "flex w-full flex-col gap-1.5 rounded-md px-3 py-3 text-left transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                selected ? "jarvis-nav-active bg-secondary" : "hover:bg-secondary",
              )}
            >
              <div className="flex items-center gap-2">
                <OutcomeDot outcome={r.outcome} />
                <span className="min-w-0 flex-1 truncate text-base font-medium">
                  {r.preview || r.session_id.slice(0, 8)}
                </span>
                <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                  {new Date(r.started_ms).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-x-1.5 pl-4 text-sm tabular-nums text-muted-foreground">
                <span>{r.turn_count} turns</span>
                {r.duration_s !== null && <span>· {r.duration_s.toFixed(1)}s</span>}
                {slow && <span className="text-warning">· slow</span>}
                {r.error_count > 0 && (
                  <span className="text-destructive">· {r.error_count} errors</span>
                )}
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
