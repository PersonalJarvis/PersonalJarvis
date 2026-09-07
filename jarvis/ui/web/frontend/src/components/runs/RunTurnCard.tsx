/**
 * One Run-turn, from the developer's angle.
 *
 * The top of the card stays readable (what was said, what came back, which
 * capabilities fired); everything a developer needs to reconstruct HOW the turn
 * was handled lives in the forensic tab strip below it — decisions with their
 * recorded rationale, the latency waterfall, tool I/O, the raw event stream and
 * errors. Tabs rather than five stacked sections, because the useful move is
 * "show me the events for THIS turn", not "scroll past four panels".
 *
 * Surfaces: the card is the object, everything nested inside it steps up to
 * --secondary once and stops there. A tab with nothing recorded is not drawn
 * at all — a disabled tab is a control that lies about being one.
 */
import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Brain, ChevronDown, ChevronRight, Hourglass, Mic2, Volume2, Zap } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { TabBar } from "@/components/layout/SectionTabBar";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

import { fmtInt, fmtMs, useRunLocale } from "./format";

import { OutcomeBadge } from "./OutcomeBadge";
import { FeatureBadges } from "./FeatureBadges";
import { LatencyWaterfall } from "./LatencyWaterfall";
import { DecisionPath } from "./DecisionPath";
import { ToolTable } from "./ToolTable";
import { ErrorPanel } from "./ErrorPanel";
import { EventStream } from "./EventStream";
import type { RunTurn, TranscriptLine } from "./types";

/**
 * A trace line's role is told by its LABEL, not by a tinted box. Only `error`
 * keeps ink, because only `error` is a status.
 */
const ROLE_INK: Record<string, string> = {
  error: "text-destructive",
};

const ROLE_LABEL: Record<string, string> = {
  jarvis: "spoken",
  system: "system",
  tool: "tool",
  error: "error",
};

type TabId = "decisions" | "latency" | "tools" | "events" | "errors";

export function RunTurnCard({ turn }: { turn: RunTurn }) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const locale = useRunLocale();
  const [showForensics, setShowForensics] = useState(false);
  const [tab, setTab] = useState<TabId>("events");

  // "What happened" = every transcript line that is NOT the headline user
  // utterance or the headline Jarvis reply (those get their own blocks), and
  // not raw state-machine churn. Carries intermediate phrases, tool/CU outcomes
  // and system outputs (exit codes, denials).
  const trace = (turn.transcript ?? []).filter(
    (l) =>
      l.kind !== "SystemStateChanged" &&
      !(l.role === "user" && l.text === turn.user_text) &&
      !(l.role === "jarvis" && l.text === turn.jarvis_text),
  );

  const triggered = [...turn.activity.agents, ...turn.activity.tools];
  // Defaulted, not assumed — a run served by an older backend must render a
  // quiet empty tab, never crash the inspector (BUG-008 degrade contract).
  const events = turn.events ?? [];

  // Only the lanes that recorded something. An empty lane is left out rather
  // than drawn greyed-out, so the strip states what exists in this turn.
  const tabs = useMemo(
    () =>
      (
        [
          { id: "decisions", label: t("run_inspector.panel.decision"), count: turn.decision_path.length },
          { id: "latency", label: t("run_inspector.panel.latency"), count: turn.latency.length },
          { id: "tools", label: t("run_inspector.panel.tools"), count: turn.tools.length },
          { id: "events", label: t("run_inspector.panel.events"), count: events.length },
          { id: "errors", label: t("run_inspector.panel.errors"), count: turn.errors.length },
        ] as Array<{ id: TabId; label: string; count: number }>
      ).filter((x) => x.count > 0),
    [t, turn.decision_path.length, turn.latency.length, turn.tools.length, turn.errors.length, events.length],
  );
  const hasForensics = tabs.length > 0;
  // The remembered tab can be a lane this turn never recorded.
  const activeTab = tabs.some((x) => x.id === tab) ? tab : (tabs[0]?.id ?? "events");
  const Chevron = showForensics ? ChevronDown : ChevronRight;

  return (
    <Card data-testid="run-turn-card">
      <CardContent className="space-y-4 p-5 pt-5">
        {/* Header: turn # + outcome + brain meta */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-semibold text-foreground-strong">
              Turn {turn.idx + 1}
            </h3>
            <OutcomeBadge outcome={turn.outcome} />
          </div>
          <div className="flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
            {turn.tier && <Badge variant="outline">{turn.tier}</Badge>}
            {(turn.model || turn.provider) && (
              <Badge variant="outline" className="font-mono">
                {turn.model || turn.provider}
              </Badge>
            )}
            {/* "Not measured" and "measured as zero" are different facts —
                printing a bare 0 for a realtime turn billed at session level
                would misreport it as free. */}
            {turn.usage_recorded ? (
              <span className="tabular-nums">
                {fmtInt(turn.tokens_in, locale)}+{fmtInt(turn.tokens_out, locale)} tok
                {turn.cost_usd > 0 && ` · $${turn.cost_usd.toFixed(4)}`}
              </span>
            ) : (
              <span>{t("run_inspector.no_usage")}</span>
            )}
          </div>
        </div>

        {/* User */}
        {turn.user_text && (
          <Block icon={<Mic2 aria-hidden className="h-3.5 w-3.5" />} label="User">
            {turn.user_text}
          </Block>
        )}

        {/* Triggered capabilities — the per-turn headline */}
        {triggered.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Zap aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="text-muted-foreground">{t("run_inspector.triggered")}</span>
            <FeatureBadges tags={triggered} />
          </div>
        )}

        {/* Assistant reply */}
        {turn.jarvis_text && (
          <Block icon={<Volume2 aria-hidden className="h-3.5 w-3.5" />} label={assistantName}>
            {turn.jarvis_text}
          </Block>
        )}

        {/* What happened — intermediate phrases, tool outcomes, system outputs */}
        {trace.length > 0 && (
          <div className="space-y-1 border-t border-border pt-4">
            <div className="text-sm text-muted-foreground">
              {t("run_inspector.what_happened")}
            </div>
            {trace.map((l, i) => (
              <TraceLine key={`${l.ts_ms}-${i}`} line={l} />
            ))}
          </div>
        )}

        {/* Turn facts — the small machine-readable truths that used to be
            recorded but never shown (endpoint reason, prompt-cache hit,
            barge-in, prompt size, the trace id you need to grep a log for). */}
        <TurnFacts turn={turn} />

        {/* Think / speak */}
        {(turn.think_ms > 0 || turn.speak_ms > 0) && (
          <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
            <span className="flex items-center gap-1.5 tabular-nums">
              <Brain aria-hidden className="h-3.5 w-3.5" /> {fmtMs(turn.think_ms)} thinking
            </span>
            <span className="flex items-center gap-1.5 tabular-nums">
              <Hourglass aria-hidden className="h-3.5 w-3.5" /> {fmtMs(turn.speak_ms)} speaking
            </span>
          </div>
        )}

        {/* Forensics — deep, on demand */}
        {hasForensics && (
          <div className="border-t border-border pt-3">
            <button
              type="button"
              data-testid="forensics-toggle"
              aria-expanded={showForensics}
              onClick={() => setShowForensics((v) => !v)}
              className={cn(
                "-ml-2 flex items-center gap-1.5 rounded-md px-2 py-1 text-sm font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              )}
            >
              <Chevron aria-hidden className="h-4 w-4" />
              {t("run_inspector.forensics")}
              <span className="tabular-nums text-foreground-faint">
                {events.length} {t("run_inspector.stream.events")}
              </span>
            </button>
            {showForensics && (
              <div className="mt-3 space-y-4">
                <TabBar
                  tabs={tabs}
                  active={activeTab}
                  onChange={(id) => setTab(id as TabId)}
                />
                <div>
                  {activeTab === "decisions" && <DecisionPath steps={turn.decision_path} />}
                  {activeTab === "latency" && <LatencyWaterfall entries={turn.latency} />}
                  {activeTab === "tools" && <ToolTable tools={turn.tools} />}
                  {activeTab === "events" && (
                    <EventStream events={events} truncated={turn.events_truncated} />
                  )}
                  {activeTab === "errors" && <ErrorPanel errors={turn.errors} />}
                </div>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function TurnFacts({ turn }: { turn: RunTurn }) {
  const t = useT();
  const locale = useRunLocale();
  const facts: Array<[string, string]> = [];
  if (turn.extras.endpoint_reason) {
    facts.push([t("run_inspector.facts.endpoint"), turn.extras.endpoint_reason]);
  }
  if (turn.extras.cache_hit !== null) {
    facts.push([
      t("run_inspector.facts.cache"),
      turn.extras.cache_hit
        ? t("run_inspector.facts.cache_hit")
        : t("run_inspector.facts.cache_miss"),
    ]);
  }
  if (turn.extras.interrupted) {
    facts.push([t("run_inspector.facts.interrupted"), "yes"]);
  }
  if (turn.extras.context_tokens) {
    facts.push([
      t("run_inspector.facts.context"),
      `${fmtInt(turn.extras.context_tokens, locale)} tok`,
    ]);
  }
  facts.push(["trace_id", turn.trace_id]);
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {facts.map(([k, v]) => (
        <span key={k} className="inline-flex items-center gap-1.5">
          <span>{k}</span>
          <span className="font-mono text-foreground-secondary">{v}</span>
        </span>
      ))}
    </div>
  );
}

/**
 * One side of the conversation. The label names the speaker; the words sit on
 * the card's lift surface so the reading block is an object, not an outline.
 */
function Block({
  icon,
  label,
  children,
}: {
  icon: ReactNode;
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
        {icon}
        {label}
      </div>
      <p className="rounded-md bg-secondary px-3 py-2.5 text-base text-foreground [overflow-wrap:anywhere]">
        {children}
      </p>
    </div>
  );
}

function TraceLine({ line }: { line: TranscriptLine }) {
  const label = line.spoken_kind || ROLE_LABEL[line.role] || line.role;
  return (
    <div className="flex items-start gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-secondary">
      <Badge variant="secondary" className="shrink-0">
        {label}
      </Badge>
      <span
        className={cn(
          "min-w-0 flex-1 break-words pt-0.5",
          ROLE_INK[line.role] ?? "text-foreground-secondary",
        )}
      >
        {line.text}
      </span>
    </div>
  );
}
