import type { ReactNode } from "react";
import type { Run } from "./types";

/** A compact labelled metric: small uppercase label over a mono value. */
export function StatChip({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  tone?: "default" | "warn" | "breach";
}) {
  const valueCls =
    tone === "breach" ? "text-rose-300" : tone === "warn" ? "text-amber-300" : "text-foreground";
  return (
    <div className="rounded-lg border border-border/70 bg-background/40 px-3 py-2">
      <div className="text-[9px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
        {label}
      </div>
      <div className={`mt-0.5 font-mono text-sm tabular-nums ${valueCls}`}>{value}</div>
    </div>
  );
}

function fmtMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}

/** Deep-dive analytics grid for a single run. */
export function MetricsPanel({ run }: { run: Run }) {
  const a = run.analytics;
  const providers = Object.entries(a.cost_by_provider);
  const tools = Object.entries(a.tool_counts).sort((x, y) => y[1] - x[1]);
  return (
    <div className="space-y-3" data-testid="metrics-panel">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
        <StatChip label="Think" value={fmtMs(a.total_think_ms)} />
        <StatChip label="Speak" value={fmtMs(a.total_speak_ms)} />
        <StatChip label="Tokens in" value={a.total_tokens_in.toLocaleString()} />
        <StatChip label="Tokens out" value={a.total_tokens_out.toLocaleString()} />
        <StatChip
          label="Interruptions"
          value={a.interruptions}
          tone={a.interruptions > 0 ? "warn" : "default"}
        />
        <StatChip
          label="Worst latency"
          value={a.worst_slo_status}
          tone={a.worst_slo_status === "breach" ? "breach" : a.worst_slo_status === "warn" ? "warn" : "default"}
        />
      </div>

      {providers.length > 0 && (
        <div>
          <div className="mb-1 text-[9px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
            Cost by provider
          </div>
          <div className="space-y-0.5">
            {providers.map(([p, c]) => (
              <div key={p} className="flex items-center justify-between text-[11px]">
                <span className="text-muted-foreground">{p}</span>
                <span className="font-mono tabular-nums">${c.toFixed(4)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {tools.length > 0 && (
        <div>
          <div className="mb-1 text-[9px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
            Tool usage
          </div>
          <div className="flex flex-wrap gap-1">
            {tools.map(([name, n]) => (
              <span
                key={name}
                className="inline-flex items-center gap-1 rounded-md bg-muted/40 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground ring-1 ring-inset ring-border/60"
              >
                {name}
                <span className="text-foreground/80">×{n}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
