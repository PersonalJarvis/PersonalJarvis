/**
 * Deep-dive analytics for a single run: the six headline numbers, then the
 * three distributions that say what the run was actually made of.
 *
 * The tiles are the app's shared `StatTile`, so a number here is drawn exactly
 * like a number on the agent board or in Spend. Only a breached SLO and a
 * barge-in carry a tone — everything else is a measurement, not a status.
 */
import { Badge } from "@/components/ui/badge";
import { StatTile } from "@/components/extensions/primitives";
import { useT } from "@/i18n";

import { fmtInt, fmtMs, useRunLocale } from "./format";
import type { Run } from "./types";

/** A group's name inside the panel. One weight, one size, no caps. */
function GroupLabel({ children }: { children: string }) {
  return (
    <h3 className="mb-2 text-base font-medium text-foreground-strong">{children}</h3>
  );
}

/** A neutral count chip: `name ×n`. */
function CountChip({ name, count }: { name: string; count: number }) {
  return (
    <Badge variant="secondary" className="font-mono">
      {name}
      <span className="tabular-nums text-foreground">×{count}</span>
    </Badge>
  );
}

export function MetricsPanel({ run }: { run: Run }) {
  const t = useT();
  const locale = useRunLocale();
  const a = run.analytics;
  const providers = Object.entries(a.cost_by_provider);
  const tools = Object.entries(a.tool_counts).sort((x, y) => y[1] - x[1]);
  const eventKinds = Object.entries(run.event_counts ?? {}).sort((x, y) => y[1] - x[1]);
  return (
    <div className="space-y-6" data-testid="metrics-panel">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <StatTile label={t("run_inspector.metrics.think")} value={fmtMs(a.total_think_ms)} />
        <StatTile label={t("run_inspector.metrics.speak")} value={fmtMs(a.total_speak_ms)} />
        <StatTile
          label={t("run_inspector.metrics.tokens_in")}
          value={fmtInt(a.total_tokens_in, locale)}
        />
        <StatTile
          label={t("run_inspector.metrics.tokens_out")}
          value={fmtInt(a.total_tokens_out, locale)}
        />
        <StatTile
          label={t("run_inspector.metrics.interruptions")}
          value={fmtInt(a.interruptions, locale)}
          tone={a.interruptions > 0 ? "warn" : "ok"}
        />
        <StatTile
          label={t("run_inspector.metrics.worst_latency")}
          value={a.worst_slo_status}
          tone={a.worst_slo_status === "breach" ? "danger" : "ok"}
        />
      </div>

      {providers.length > 0 && (
        <section>
          <GroupLabel>{t("run_inspector.metrics.cost_by_provider")}</GroupLabel>
          <dl className="space-y-1.5">
            {providers.map(([p, c]) => (
              <div key={p} className="flex items-baseline justify-between gap-3 text-sm">
                <dt className="min-w-0 truncate text-muted-foreground">{p}</dt>
                <dd className="shrink-0 font-mono tabular-nums text-foreground">
                  ${c.toFixed(4)}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      {/* Event-kind histogram — the fastest read on what dominated this run.
          A run that is 90% LatencySpan looks very different from one that is
          40% CUStepProfiled, and the shape alone often locates a problem. */}
      {eventKinds.length > 0 && (
        <section>
          <GroupLabel>{t("run_inspector.metrics.recorded_events")}</GroupLabel>
          <div className="flex flex-wrap gap-1" data-testid="event-histogram">
            {eventKinds.map(([kind, n]) => (
              <CountChip key={kind} name={kind} count={n} />
            ))}
          </div>
        </section>
      )}

      {tools.length > 0 && (
        <section>
          <GroupLabel>{t("run_inspector.metrics.tool_usage")}</GroupLabel>
          <div className="flex flex-wrap gap-1">
            {tools.map(([name, n]) => (
              <CountChip key={name} name={name} count={n} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
