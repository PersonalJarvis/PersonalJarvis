import type { ReactNode } from "react";

import { fmtInt, fmtMs, useRunLocale } from "./format";
import type { Run } from "./types";

/**
 * One measured number with the word for it underneath.
 *
 * A real tile — `.jarvis-stat-tile` is the shared lift surface — rather than
 * the outlined transparent box it used to be, and the label is sentence case
 * at the type floor instead of a 9px letter-spaced all-caps line. `breach`
 * is the only tone carrying hue, because it is the only one that is a fault.
 */
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
    tone === "breach"
      ? "text-destructive"
      : tone === "warn"
        ? "text-warning"
        : "text-foreground-strong";
  return (
    <div className="jarvis-stat-tile">
      <div className={`font-mono text-title tabular-nums ${valueCls}`}>{value}</div>
      <div className="mt-0.5 text-micro text-muted-foreground">{label}</div>
    </div>
  );
}

/** A group's name. One weight, one size, no letter-spaced caps. */
function GroupLabel({ children }: { children: ReactNode }) {
  return (
    <div className="mb-stack text-title font-semibold text-foreground-strong">
      {children}
    </div>
  );
}

/** A neutral count chip: `name ×n`. */
function CountChip({ name, count }: { name: string; count: number }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-secondary px-2 py-0.5 font-mono text-micro text-muted-foreground">
      {name}
      <span className="tabular-nums text-foreground">×{count}</span>
    </span>
  );
}

/** Deep-dive analytics grid for a single run. */
export function MetricsPanel({ run }: { run: Run }) {
  const locale = useRunLocale();
  const a = run.analytics;
  const providers = Object.entries(a.cost_by_provider);
  const tools = Object.entries(a.tool_counts).sort((x, y) => y[1] - x[1]);
  const eventKinds = Object.entries(run.event_counts ?? {}).sort((x, y) => y[1] - x[1]);
  return (
    <div className="space-y-group" data-testid="metrics-panel">
      <div className="grid grid-cols-2 gap-stack sm:grid-cols-3 lg:grid-cols-4">
        <StatChip label="Think" value={fmtMs(a.total_think_ms)} />
        <StatChip label="Speak" value={fmtMs(a.total_speak_ms)} />
        <StatChip label="Tokens in" value={fmtInt(a.total_tokens_in, locale)} />
        <StatChip label="Tokens out" value={fmtInt(a.total_tokens_out, locale)} />
        <StatChip
          label="Interruptions"
          value={a.interruptions}
          tone={a.interruptions > 0 ? "warn" : "default"}
        />
        <StatChip
          label="Worst latency"
          value={a.worst_slo_status}
          tone={
            a.worst_slo_status === "breach"
              ? "breach"
              : a.worst_slo_status === "warn"
                ? "warn"
                : "default"
          }
        />
      </div>

      {providers.length > 0 && (
        <div>
          <GroupLabel>Cost by provider</GroupLabel>
          <div className="space-y-1">
            {providers.map(([p, c]) => (
              <div key={p} className="flex items-center justify-between text-body">
                <span className="text-muted-foreground">{p}</span>
                <span className="font-mono tabular-nums text-foreground">
                  ${c.toFixed(4)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Event-kind histogram — the fastest read on what dominated this run.
          A run that is 90% LatencySpan looks very different from one that is
          40% CUStepProfiled, and the shape alone often locates a problem. */}
      {eventKinds.length > 0 && (
        <div>
          <GroupLabel>Recorded events</GroupLabel>
          <div className="flex flex-wrap gap-1" data-testid="event-histogram">
            {eventKinds.map(([kind, n]) => (
              <CountChip key={kind} name={kind} count={n} />
            ))}
          </div>
        </div>
      )}

      {tools.length > 0 && (
        <div>
          <GroupLabel>Tool usage</GroupLabel>
          <div className="flex flex-wrap gap-1">
            {tools.map(([name, n]) => (
              <CountChip key={name} name={name} count={n} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
