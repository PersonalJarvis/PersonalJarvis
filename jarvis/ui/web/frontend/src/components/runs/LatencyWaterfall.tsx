import { cn } from "@/lib/utils";

import type { LatencyEntry } from "./types";

/**
 * Per-phase latency, one bar each. The three SLO states are the three status
 * hues: a phase inside budget is alive, a warned one is degraded, a breached
 * one is a fault. Nothing else in the chart carries colour, so the eye lands
 * on the phase that actually missed its budget.
 */
const BAR: Record<string, string> = {
  ok: "bg-success",
  warn: "bg-warning",
  breach: "bg-destructive",
};

export function LatencyWaterfall({ entries }: { entries: LatencyEntry[] }) {
  if (entries.length === 0) {
    return <p className="text-base text-muted-foreground">n/a</p>;
  }
  const max = Math.max(...entries.map((e) => e.duration_ms), 1);
  return (
    <div className="space-y-1.5">
      {entries.map((e) => (
        <div
          key={e.phase}
          className="flex items-center gap-3"
          data-testid={`lat-${e.phase}`}
          data-slo={e.slo_status}
        >
          <span className="w-44 shrink-0 truncate font-mono text-sm text-muted-foreground">
            {e.phase}
          </span>
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-secondary">
            <div
              className={cn("h-full rounded-full", BAR[e.slo_status] ?? BAR.ok)}
              style={{ width: `${Math.max(3, (e.duration_ms / max) * 100)}%` }}
            />
          </div>
          <span className="w-16 shrink-0 text-right font-mono text-sm tabular-nums text-foreground">
            {e.duration_ms.toFixed(0)}ms
          </span>
        </div>
      ))}
    </div>
  );
}
