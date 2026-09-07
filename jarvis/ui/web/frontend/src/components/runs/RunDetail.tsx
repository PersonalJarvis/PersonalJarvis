/**
 * One recorded run, end to end.
 *
 * The header answers "what am I looking at" in one line — what was asked, how
 * it ended, when, how long, what it cost — and everything deeper is a tab, so
 * the forensic material is one click away instead of five stacked disclosures.
 * The turn stream is the default tab because that is the read a developer
 * opens a run for; environment, metrics, faults and the session frame sit
 * beside it.
 */
import { useEffect, useState } from "react";
import { ArrowLeft, Download } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TabBar } from "@/components/layout/SectionTabBar";
import { PanelSkeleton, SkeletonBar } from "@/components/layout/PanelSkeleton";
import { useRunDetail } from "@/hooks/useRuns";
import { useT } from "@/i18n";

import { runExportUrl } from "./api";
import { EnvironmentPanel } from "./EnvironmentPanel";
import { RunErrorList } from "./ErrorPanel";
import { EventStream } from "./EventStream";
import { FeatureBadges } from "./FeatureBadges";
import { MetricsPanel } from "./MetricsPanel";
import { OutcomeBadge } from "./OutcomeBadge";
import { RunTurnCard } from "./RunTurnCard";
import { fmtInt, useRunLocale } from "./format";
import type { RunEnvironment } from "./types";

const EMPTY_ENV: RunEnvironment = {
  voice_mode: "", surface: "", wake_source: "", wake_keyword: "", language: "",
  hangup_reason: "", providers: [], models: [], tiers: [], voices: [],
  input_sample_rate: null, output_sample_rate: null,
};

type TabId = "turns" | "environment" | "metrics" | "errors" | "session_events";

export function RunDetail({
  sessionId,
  onBack,
}: {
  sessionId: string;
  /** Narrow layouts only: return to the run list. */
  onBack?: () => void;
}) {
  const t = useT();
  const locale = useRunLocale();
  const { data: run, isLoading } = useRunDetail(sessionId);
  const [tab, setTab] = useState<TabId>("turns");

  // A different run starts on its own default tab — "Errors" from the previous
  // run would otherwise open on one that recorded none.
  useEffect(() => setTab("turns"), [sessionId]);

  if (isLoading || !run) {
    // The real column at its real height with bars where the turn cards go —
    // a centred "…" in a black rectangle is indistinguishable from a section
    // that failed to load.
    return (
      <div className="flex h-full min-h-0 flex-col" data-testid="run-detail-loading">
        <div className="shrink-0 border-b border-border px-6 py-5">
          <div className="mx-auto w-full max-w-3xl space-y-2">
            <SkeletonBar className="h-7 w-64" />
            <SkeletonBar className="h-4 w-80" />
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-hidden px-6 py-5">
          <div className="mx-auto w-full max-w-3xl">
            <PanelSkeleton rows={4} rowHeight={148} label={t("run_inspector.loading")} />
          </div>
        </div>
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
  const sessionEvents = run.session_events ?? [];
  const totalEvents = Object.values(run.event_counts ?? {}).reduce((s, n) => s + n, 0);
  const errorCount = run.turns.reduce((s, turn) => s + turn.errors.length, 0);

  // The run's own name: what was actually asked. A run with no captured
  // utterance falls back to its recorded start, never to a bare id.
  const headline = run.turns.find((x) => x.user_text.trim())?.user_text.trim();
  const title = headline || started.toLocaleString(locale);

  const facts = [
    headline ? started.toLocaleString(locale) : null,
    ended ? ended.toLocaleTimeString(locale) : null,
    `${run.turns.length} ${t("run_inspector.facts.turns")}`,
    a.total_duration_s !== null ? `${a.total_duration_s.toFixed(1)}s` : null,
    run.session.total_cost_usd > 0 ? `$${run.session.total_cost_usd.toFixed(3)}` : null,
    tokens > 0 ? `${fmtInt(tokens, locale)} ${t("run_inspector.facts.tokens")}` : null,
    totalEvents > 0
      ? `${fmtInt(totalEvents, locale)} ${t("run_inspector.stream.events")}`
      : null,
    run.session.hangup_reason || null,
  ].filter(Boolean);

  const tabs = [
    { id: "turns", label: t("run_inspector.tab.turns"), count: run.turns.length },
    { id: "environment", label: t("run_inspector.tab.environment") },
    { id: "metrics", label: t("run_inspector.tab.metrics") },
    ...(errorCount > 0
      ? [{ id: "errors", label: t("run_inspector.tab.errors"), count: errorCount }]
      : []),
    ...(sessionEvents.length > 0
      ? [
          {
            id: "session_events",
            label: t("run_inspector.tab.session_events"),
            count: sessionEvents.length,
          },
        ]
      : []),
  ];
  const activeTab = tabs.some((x) => x.id === tab) ? tab : "turns";

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="run-detail">
      {/* ── Run header ───────────────────────────────────────────── */}
      <div className="shrink-0 border-b border-border px-6 pt-5">
        <div className="mx-auto w-full max-w-3xl">
          {onBack && (
            <Button variant="ghost" size="sm" onClick={onBack} className="-ml-3 mb-2">
              <ArrowLeft aria-hidden />
              {t("run_inspector.back")}
            </Button>
          )}
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 items-center gap-2">
                <h2
                  className="truncate text-xl font-semibold text-foreground-strong"
                  title={title}
                >
                  {title}
                </h2>
                <OutcomeBadge outcome={run.outcome} />
                {a.worst_slo_status !== "ok" && (
                  <Badge
                    variant={a.worst_slo_status === "breach" ? "destructive" : "warning"}
                  >
                    {t("run_inspector.latency")} {a.worst_slo_status}
                  </Badge>
                )}
              </div>
              <p className="mt-1 truncate text-sm tabular-nums text-muted-foreground">
                {facts.join(" · ")}
              </p>
            </div>
            <Button asChild variant="outline" size="sm">
              <a href={runExportUrl(sessionId)} target="_blank" rel="noreferrer">
                <Download aria-hidden />
                {t("run_inspector.export_raw")}
              </a>
            </Button>
          </div>

          {tags.length > 0 && (
            <div className="mt-3">
              <FeatureBadges tags={tags} />
            </div>
          )}

          <TabBar
            className="mt-4"
            tabs={tabs}
            active={activeTab}
            onChange={(id) => setTab(id as TabId)}
          />
        </div>
      </div>

      {/* ── Scrollable body (one reading column) ─────────────────── */}
      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-6 py-5">
        <div className="mx-auto w-full max-w-3xl">
          {activeTab === "turns" &&
            (run.turns.length === 0 ? (
              // A recorded session that never completed a turn: real, and not
              // the same thing as a section that failed to render.
              <p className="text-base text-muted-foreground">
                {t("run_inspector.no_turns")}
              </p>
            ) : (
              <div className="space-y-3">
                {run.turns.map((turn) => (
                  <RunTurnCard key={turn.trace_id} turn={turn} />
                ))}
              </div>
            ))}
          {activeTab === "environment" && <EnvironmentPanel env={env} />}
          {activeTab === "metrics" && <MetricsPanel run={run} />}
          {activeTab === "errors" && <RunErrorList turns={run.turns} />}
          {activeTab === "session_events" && <EventStream events={sessionEvents} />}
        </div>
      </div>
    </div>
  );
}
