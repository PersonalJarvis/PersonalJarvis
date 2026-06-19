/**
 * One Run-turn, fully visible (modeled on the Transcription TurnCard) and
 * enriched with the forensic lens: what the user said, what Jarvis answered,
 * and — the headline — exactly WHICH capabilities/agents/tools this turn
 * triggered. Deep forensics (latency, decision path, timeline, errors) stay one
 * click away so the card reads cleanly at a glance.
 */
import { useState } from "react";
import type { ReactNode } from "react";
import { Brain, Hourglass, Mic2, Volume2, Zap } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { useT } from "@/i18n";

import { OutcomeBadge } from "./OutcomeBadge";
import { FeatureBadges } from "./FeatureBadges";
import { TimelinePanel } from "./TimelinePanel";
import { LatencyWaterfall } from "./LatencyWaterfall";
import { DecisionPath } from "./DecisionPath";
import { ToolTable } from "./ToolTable";
import { ErrorPanel } from "./ErrorPanel";
import type { RunTurn, TranscriptLine } from "./types";

const ROLE_TONE: Record<string, string> = {
  jarvis: "border-primary/20 bg-primary/5",
  system: "border-border bg-muted/30",
  tool: "border-sky-400/20 bg-sky-400/5",
  error: "border-destructive/30 bg-destructive/10",
};

const ROLE_LABEL: Record<string, string> = {
  jarvis: "spoken",
  system: "system",
  tool: "tool",
  error: "error",
};

export function RunTurnCard({ turn }: { turn: RunTurn }) {
  const t = useT();
  const [showForensics, setShowForensics] = useState(false);

  // "What happened" = every transcript line that is NOT the headline user
  // utterance or the headline Jarvis reply (those get their own blocks), and
  // not raw state-machine churn. Carries intermediate phrases, tool/CU outcomes
  // and system outputs (exit codes, "das hat nicht geklappt", denials). i18n-allow
  const trace = (turn.transcript ?? []).filter(
    (l) =>
      l.kind !== "SystemStateChanged" &&
      !(l.role === "user" && l.text === turn.user_text) &&
      !(l.role === "jarvis" && l.text === turn.jarvis_text),
  );

  const triggered = [...turn.activity.agents, ...turn.activity.tools];
  const hasForensics =
    turn.latency.length > 0 ||
    turn.decision_path.length > 0 ||
    turn.timeline.length > 0 ||
    turn.errors.length > 0;

  return (
    <Card className="bg-background/40" data-testid="run-turn-card">
      <CardContent className="space-y-3 p-4">
        {/* Header: turn # + outcome + brain meta */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-semibold">Turn {turn.idx + 1}</span>
            <OutcomeBadge outcome={turn.outcome} />
          </div>
          <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
            {turn.tier && (
              <Badge variant="outline" className="text-[10px]">{turn.tier}</Badge>
            )}
            {(turn.model || turn.provider) && (
              <Badge variant="outline" className="font-mono text-[10px]">
                {turn.model || turn.provider}
              </Badge>
            )}
            {(turn.tokens_in > 0 || turn.tokens_out > 0) && (
              <span>{turn.tokens_in}+{turn.tokens_out} tok</span>
            )}
            {turn.cost_usd > 0 && <span>· ${turn.cost_usd.toFixed(3)}</span>}
          </div>
        </div>

        {/* User */}
        {turn.user_text && (
          <Block icon={<Mic2 className="h-3 w-3" />} label="User" accent="text-emerald-400"
                 box="border-emerald-400/20 bg-emerald-400/5">
            {turn.user_text}
          </Block>
        )}

        {/* Triggered capabilities — the per-turn headline */}
        {triggered.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-amber-400/20 bg-amber-400/5 px-2 py-1.5 text-[11px]">
            <Zap className="h-3.5 w-3.5 shrink-0 text-amber-400" />
            <span className="font-medium uppercase tracking-wider text-amber-300/90">
              {t("run_inspector.triggered")}
            </span>
            <FeatureBadges tags={triggered} />
          </div>
        )}

        {/* Jarvis */}
        {turn.jarvis_text && (
          <Block icon={<Volume2 className="h-3 w-3" />} label="Jarvis" accent="text-primary"
                 box="border-primary/20 bg-primary/5">
            {turn.jarvis_text}
          </Block>
        )}

        {/* What happened — intermediate phrases, tool outcomes, system outputs */}
        {trace.length > 0 && (
          <div className="space-y-1.5 border-t border-border/50 pt-2">
            <div className="text-[11px] uppercase tracking-wider text-muted-foreground">
              {t("run_inspector.what_happened")}
            </div>
            {trace.map((l, i) => (
              <TraceLine key={`${l.ts_ms}-${i}`} line={l} />
            ))}
          </div>
        )}

        {/* Think / speak */}
        {(turn.think_ms > 0 || turn.speak_ms > 0) && (
          <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <Brain className="h-3 w-3 text-amber-300" /> {fmtMs(turn.think_ms)} thinking
            </span>
            <span className="flex items-center gap-1">
              <Hourglass className="h-3 w-3 text-primary" /> {fmtMs(turn.speak_ms)} speaking
            </span>
          </div>
        )}

        {/* Forensics — deep, on demand */}
        {hasForensics && (
          <div className="border-t border-border/50 pt-2">
            <button
              type="button"
              data-testid="forensics-toggle"
              onClick={() => setShowForensics((v) => !v)}
              className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              <span>{showForensics ? "▾" : "▸"}</span>
              {t("run_inspector.forensics")}
            </button>
            {showForensics && (
              <div className="mt-2 space-y-3 text-xs">
                <Section label={t("run_inspector.panel.latency")}>
                  <LatencyWaterfall entries={turn.latency} />
                </Section>
                <Section label={t("run_inspector.panel.decision")}>
                  <DecisionPath steps={turn.decision_path} />
                </Section>
                <Section label={t("run_inspector.panel.tools")}>
                  <ToolTable tools={turn.tools} />
                </Section>
                <Section label={t("run_inspector.panel.timeline")}>
                  <TimelinePanel turn={turn} />
                </Section>
                {turn.errors.length > 0 && (
                  <Section label={t("run_inspector.panel.errors")}>
                    <ErrorPanel errors={turn.errors} />
                  </Section>
                )}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Block({
  icon, label, accent, box, children,
}: {
  icon: ReactNode; label: string; accent: string; box: string; children: ReactNode;
}) {
  return (
    <div className="space-y-1">
      <div className={`flex items-center gap-1.5 text-[11px] uppercase tracking-wider ${accent}`}>
        {icon}
        {label}
      </div>
      <div className={`rounded-md border p-2 text-sm leading-relaxed ${box}`}>{children}</div>
    </div>
  );
}

function TraceLine({ line }: { line: TranscriptLine }) {
  const tone = ROLE_TONE[line.role] ?? "border-border bg-muted/20";
  const label = line.spoken_kind || ROLE_LABEL[line.role] || line.role;
  return (
    <div className={`flex items-start gap-2 rounded-md border p-2 text-[13px] ${tone}`}>
      <Badge variant="secondary" className="mt-0.5 shrink-0 text-[9px] uppercase tracking-wide">
        {label}
      </Badge>
      <span className="min-w-0 flex-1 break-words">{line.text}</span>
    </div>
  );
}

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      {children}
    </div>
  );
}

function fmtMs(ms: number): string {
  if (ms <= 0) return "0ms";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}
