import type { LatencyEntry } from "./types";

/**
 * Per-phase latency, one bar each. The three SLO states are the three status
 * hues: a phase inside budget is alive, a warned one is degraded, a breached
 * one is a fault. `warn` used to be painted in --foreground, which made the
 * middle state the brightest bar in the chart.
 */
const BAR: Record<string, string> = {
  ok: "bg-success",
  warn: "bg-warning",
  breach: "bg-destructive",
};

export function LatencyWaterfall({ entries }: { entries: LatencyEntry[] }) {
  if (entries.length === 0)
    return <span className="text-body text-muted-foreground">n/a</span>;
  const max = Math.max(...entries.map((e) => e.duration_ms), 1);
  return (
    <div className="space-y-1">
      {entries.map((e) => (
        <div
          key={e.phase}
          className="flex items-center gap-2"
          data-testid={`lat-${e.phase}`}
          data-slo={e.slo_status}
        >
          <span className="w-40 shrink-0 truncate font-mono text-micro text-muted-foreground">
            {e.phase}
          </span>
          <div className="h-2 flex-1 rounded-full bg-secondary">
            <div
              className={`h-2 rounded-full ${BAR[e.slo_status] ?? BAR.ok}`}
              style={{ width: `${Math.max(3, (e.duration_ms / max) * 100)}%` }}
            />
          </div>
          <span className="w-14 shrink-0 text-right font-mono text-micro tabular-nums text-foreground">
            {e.duration_ms.toFixed(0)}ms
          </span>
        </div>
      ))}
    </div>
  );
}
