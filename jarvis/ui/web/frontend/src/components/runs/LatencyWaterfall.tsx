import type { LatencyEntry } from "./types";

const BAR: Record<string, string> = {
  ok: "bg-emerald-400/70", warn: "bg-amber-400/80", breach: "bg-destructive/80",
};

export function LatencyWaterfall({ entries }: { entries: LatencyEntry[] }) {
  if (entries.length === 0) return <span className="text-muted-foreground/60">n/a</span>;
  const max = Math.max(...entries.map((e) => e.duration_ms), 1);
  return (
    <div className="space-y-1">
      {entries.map((e) => (
        <div key={e.phase} className="flex items-center gap-2"
             data-testid={`lat-${e.phase}`} data-slo={e.slo_status}>
          <span className="w-40 shrink-0 truncate font-mono text-[10px]">{e.phase}</span>
          <div className="h-2 flex-1 rounded bg-background">
            <div className={`h-2 rounded ${BAR[e.slo_status] ?? BAR.ok}`}
                 style={{ width: `${Math.max(3, (e.duration_ms / max) * 100)}%` }} />
          </div>
          <span className="w-14 shrink-0 text-right font-mono text-[10px]">
            {e.duration_ms.toFixed(0)}ms
          </span>
        </div>
      ))}
    </div>
  );
}
