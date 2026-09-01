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
 * the lift token once and stops there — the forensic panel deliberately keeps
 * NO fill so the rows inside it still have a hover to travel to.
 */
import { useState } from "react";
import type { ReactNode } from "react";
import { Brain, Hourglass, Mic2, Volume2, Zap } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { useEventStore } from "@/store/events";
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
 * A trace line's role is told by its LABEL, not by a tinted box — four boxes
 * in four hues around four one-line strings was the loudest thing on the card
 * and said less than the word already printed inside each badge. Only `error`
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
  const tabs: Array<{ id: TabId; label: string; count: number }> = [
    {
      id: "decisions",
      label: t("run_inspector.panel.decision"),
      count: turn.decision_path.length,
    },
    { id: "latency", label: t("run_inspector.panel.latency"), count: turn.latency.length },
    { id: "tools", label: t("run_inspector.panel.tools"), count: turn.tools.length },
    { id: "events", label: t("run_inspector.panel.events"), count: events.length },
    { id: "errors", label: t("run_inspector.panel.errors"), count: turn.errors.length },
  ];
  const hasForensics = tabs.some((x) => x.count > 0);

  return (
    <Card data-testid="run-turn-card">
      <CardContent className="space-y-stack p-5">
        {/* Header: turn # + outcome + brain meta */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="font-mono text-title font-semibold text-foreground-strong">
              Turn {turn.idx + 1}
            </span>
            <OutcomeBadge outcome={turn.outcome} />
          </div>
          <div className="flex flex-wrap items-center gap-1.5 text-meta text-muted-foreground">
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
              <>
                <span className="tabular-nums">
                  {fmtInt(turn.tokens_in, locale)}+{fmtInt(turn.tokens_out, locale)} tok
                </span>
                {turn.cost_usd > 0 && (
                  <span className="tabular-nums">· ${turn.cost_usd.toFixed(4)}</span>
                )}
              </>
            ) : (
              <span className="italic">{t("run_inspector.no_usage")}</span>
            )}
          </div>
        </div>

        {/* User */}
        {turn.user_text && (
          <Block icon={<Mic2 className="h-3.5 w-3.5" />} label="User">
            {turn.user_text}
          </Block>
        )}

        {/* Triggered capabilities — the per-turn headline */}
        {triggered.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 text-meta">
            <Zap className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="text-muted-foreground">{t("run_inspector.triggered")}</span>
            <FeatureBadges tags={triggered} />
          </div>
        )}

        {/* Assistant reply */}
        {turn.jarvis_text && (
          <Block icon={<Volume2 className="h-3.5 w-3.5" />} label={assistantName}>
            {turn.jarvis_text}
          </Block>
        )}

        {/* What happened — intermediate phrases, tool outcomes, system outputs */}
        {trace.length > 0 && (
          <div className="space-y-1 border-t border-border pt-stack">
            <div className="text-meta text-muted-foreground">
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
          <div className="flex flex-wrap items-center gap-3 text-meta text-muted-foreground">
            <span className="flex items-center gap-1 tabular-nums">
              <Brain className="h-3.5 w-3.5" /> {fmtMs(turn.think_ms)} thinking
            </span>
            <span className="flex items-center gap-1 tabular-nums">
              <Hourglass className="h-3.5 w-3.5" /> {fmtMs(turn.speak_ms)} speaking
            </span>
          </div>
        )}

        {/* Forensics — deep, on demand */}
        {hasForensics && (
          <div className="border-t border-border pt-stack">
            <button
              type="button"
              data-testid="forensics-toggle"
              onClick={() => setShowForensics((v) => !v)}
              className="flex items-center gap-1.5 text-meta font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              <span>{showForensics ? "▾" : "▸"}</span>
              {t("run_inspector.forensics")}
              <span className="tabular-nums">
                · {events.length} {t("run_inspector.stream.events")}
              </span>
            </button>
            {showForensics && (
              <div className="mt-stack space-y-stack">
                <div className="flex flex-wrap gap-1" role="tablist">
                  {tabs.map((x) => (
                    <button
                      key={x.id}
                      type="button"
                      role="tab"
                      aria-selected={tab === x.id}
                      data-testid={`forensic-tab-${x.id}`}
                      onClick={() => setTab(x.id)}
                      disabled={x.count === 0}
                      className={`rounded-md px-2 py-1 text-meta font-medium transition-colors ${
                        tab === x.id
                          ? "bg-secondary text-foreground-strong"
                          : x.count === 0
                            ? "text-faint-foreground"
                            : "text-muted-foreground hover:bg-secondary hover:text-foreground"
                      }`}
                    >
                      {x.label}
                      <span className="ml-1 tabular-nums text-muted-foreground">
                        {x.count}
                      </span>
                    </button>
                  ))}
                </div>
                <div>
                  {tab === "decisions" && <DecisionPath steps={turn.decision_path} />}
                  {tab === "latency" && <LatencyWaterfall entries={turn.latency} />}
                  {tab === "tools" && <ToolTable tools={turn.tools} />}
                  {tab === "events" && (
                    <EventStream events={events} truncated={turn.events_truncated} />
                  )}
                  {tab === "errors" && <ErrorPanel errors={turn.errors} />}
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
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-muted-foreground">
      {facts.map(([k, v]) => (
        <span key={k} className="inline-flex items-center gap-1">
          <span>{k}</span>
          <span className="font-mono text-foreground">{v}</span>
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
    <div className="space-y-1">
      <div className="flex items-center gap-1.5 text-meta text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="rounded-md bg-secondary p-stack text-reading text-foreground">
        {children}
      </div>
    </div>
  );
}

function TraceLine({ line }: { line: TranscriptLine }) {
  const label = line.spoken_kind || ROLE_LABEL[line.role] || line.role;
  return (
    <div className="flex items-start gap-2 rounded-md px-2 py-1.5 text-meta transition-colors hover:bg-secondary">
      <Badge variant="secondary" className="mt-px shrink-0">
        {label}
      </Badge>
      <span
        className={`min-w-0 flex-1 break-words ${ROLE_INK[line.role] ?? "text-foreground"}`}
      >
        {line.text}
      </span>
    </div>
  );
}
