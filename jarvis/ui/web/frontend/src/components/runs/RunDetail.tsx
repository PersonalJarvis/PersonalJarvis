import { useState } from "react";
import type { ReactNode } from "react";
import { useRunDetail } from "@/hooks/useRuns";
import { Badge } from "@/components/ui/badge";
import { PanelSkeleton } from "@/components/layout/PanelSkeleton";
import { runExportUrl } from "@/components/runs/api";
import { RunTurnCard } from "@/components/runs/RunTurnCard";
import { OutcomeBadge } from "@/components/runs/OutcomeBadge";
import { FeatureBadges } from "@/components/runs/FeatureBadges";
import { MetricsPanel } from "@/components/runs/MetricsPanel";
import { EnvironmentPanel } from "@/components/runs/EnvironmentPanel";
import { EventStream } from "@/components/runs/EventStream";
import type { RunEnvironment } from "@/components/runs/types";
import { useRunLocale } from "@/components/runs/format";
import { useT } from "@/i18n";

const EMPTY_ENV: RunEnvironment = {
  voice_mode: "", surface: "", wake_source: "", wake_keyword: "", language: "",
  hangup_reason: "", providers: [], models: [], tiers: [], voices: [],
  input_sample_rate: null, output_sample_rate: null,
};

export function RunDetail({ sessionId }: { sessionId: string }) {
  const t = useT();
  const locale = useRunLocale();
  const { data: run, isLoading } = useRunDetail(sessionId);
  const [showMetrics, setShowMetrics] = useState(false);
  const [showEnv, setShowEnv] = useState(false);
  const [showSessionEvents, setShowSessionEvents] = useState(false);
  if (isLoading || !run) {
    // The real column at its real height with bars where the turn cards go —
    // a centred "…" in a black rectangle is indistinguishable from a section
    // that failed to load.
    return (
      <div className="mx-auto w-full max-w-reading p-5">
        <PanelSkeleton rows={4} rowHeight={132} label={t("run_inspector.title")} />
      </div>
    );
  }

  const a = run.analytics;
  const started = new Date(run.session.started_ms);
  const ended = run.session.ended_ms ? new Date(run.session.ended_ms) : null;
  const tags = [
    ...run.activity.agents,
    ...run.activity.tools.filter((x) => !run.activity.agents.includes(x)),
  ];
  const tokens = a.total_tokens_in + a.total_tokens_out;
  // Defaulted, not assumed: the desktop shell can briefly talk to a backend
  // that predates these fields (mid-update, or a run loaded from an older
  // store). A missing slice must render as "nothing to show", never crash the
  // whole inspector — the BUG-008 degrade-don't-throw contract.
  const env: RunEnvironment = run.environment ?? EMPTY_ENV;
  const eventCounts = run.event_counts ?? {};
  const sessionEvents = run.session_events ?? [];
  const totalEvents = Object.values(eventCounts).reduce((s, n) => s + n, 0);

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="run-detail">
      {/* ── Run header (sticky) ─────────────────────────────────── */}
      <div className="shrink-0 border-b border-border px-5 py-4">
        <div className="mx-auto flex w-full max-w-reading items-start justify-between gap-3">
          <div className="min-w-0 space-y-2">
            <div className="flex items-center gap-2">
              <OutcomeBadge outcome={run.outcome} />
              <span className="font-mono text-meta tabular-nums text-muted-foreground">
                {started.toLocaleString(locale)}
                {ended && ` — ${ended.toLocaleTimeString(locale)}`}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="secondary">{run.turns.length} turns</Badge>
              {a.total_duration_s !== null && (
                <Badge variant="outline">{a.total_duration_s.toFixed(1)}s</Badge>
              )}
              {run.session.total_cost_usd > 0 && (
                <Badge variant="outline">${run.session.total_cost_usd.toFixed(3)}</Badge>
              )}
              {tokens > 0 && (
                <Badge variant="outline">{tokens.toLocaleString(locale)} tok</Badge>
              )}
              {run.session.hangup_reason && (
                <Badge variant="outline">{run.session.hangup_reason}</Badge>
              )}
              {totalEvents > 0 && (
                <Badge variant="outline">
                  {totalEvents.toLocaleString(locale)} {t("run_inspector.stream.events")}
                </Badge>
              )}
              <LatencyChip status={a.worst_slo_status} />
            </div>
            {/* The run's recorded setup, always visible: mode + provider decide
                how every number below should be read. */}
            <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
              {[env.voice_mode, env.wake_source, env.language, ...env.providers, ...env.models]
                .filter(Boolean)
                .map((v, i) => (
                  <span
                    key={`${v}-${i}`}
                    className="rounded-full bg-secondary px-2 py-0.5 font-mono text-micro text-muted-foreground"
                  >
                    {v}
                  </span>
                ))}
            </div>
            {tags.length > 0 && (
              <div className="pt-0.5">
                <FeatureBadges tags={tags} />
              </div>
            )}
          </div>

          <a
            className="shrink-0 rounded-md px-2 py-1 text-meta text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            href={runExportUrl(sessionId)}
            target="_blank"
            rel="noreferrer"
          >
            {t("run_inspector.export_raw")}
          </a>
        </div>
      </div>

      {/* ── Scrollable body (centered reading column) ───────────── */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-reading space-y-stack p-5">
          <Collapsible
            label={t("run_inspector.environment")}
            open={showEnv}
            onToggle={() => setShowEnv((v) => !v)}
            testId="environment-toggle"
          >
            <EnvironmentPanel env={env} />
          </Collapsible>

          <Collapsible
            label={t("run_inspector.deep_dive")}
            open={showMetrics}
            onToggle={() => setShowMetrics((v) => !v)}
          >
            <MetricsPanel run={run} />
          </Collapsible>

          {/* Events the recorder stored WITHOUT a turn id — the session frame
              (wake, session open/close, provider switches between turns). They
              belong to no turn card and were previously dropped entirely. */}
          {sessionEvents.length > 0 && (
            <Collapsible
              label={`${t("run_inspector.session_events")} · ${sessionEvents.length}`}
              open={showSessionEvents}
              onToggle={() => setShowSessionEvents((v) => !v)}
              testId="session-events-toggle"
            >
              <EventStream events={sessionEvents} />
            </Collapsible>
          )}

          {run.turns.map((turn) => (
            <RunTurnCard key={turn.trace_id} turn={turn} />
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * The worst latency this run reached, shown only when it is not "ok".
 * `warn` is degraded and `breach` is a fault — it used to be drawn in
 * --foreground, which made a warning the brightest chip in the header.
 */
function LatencyChip({ status }: { status: string }) {
  if (status === "ok") return null;
  return (
    <Badge variant={status === "breach" ? "fault" : "degraded"}>latency {status}</Badge>
  );
}

/**
 * A disclosure row. No box: the header is a row that hovers and the body is
 * separated by the padding it opens into, which is what the rest of the app
 * does. An outline around a closed section is chrome around nothing.
 */
function Collapsible({
  label, open, onToggle, children, testId = "metrics-toggle",
}: {
  label: string; open: boolean; onToggle: () => void; children: ReactNode; testId?: string;
}) {
  return (
    <div>
      <button
        type="button"
        data-testid={testId}
        onClick={onToggle}
        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-body font-medium text-foreground transition-colors hover:bg-secondary"
      >
        <span className="text-muted-foreground">{open ? "▾" : "▸"}</span>
        {label}
      </button>
      {open && (
        <div
          data-testid={testId.replace(/-toggle$/, "")}
          className="px-2 pb-stack pt-stack"
        >
          {children}
        </div>
      )}
    </div>
  );
}
