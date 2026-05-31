/**
 * Critic-Verdicts pro selektierter Mission.
 *
 * Pro Verdict eine Card mit:
 *  - Header: verdict-Badge (approve/revise/reject) + iteration + confidence-Bar
 *  - 4-Axes-Grid: correctness/completeness/side_effects/security mit pass/fail
 *  - Pro Achse collabsable Evidence-List
 */
import { useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ListChecks,
  Shield,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type {
  CriticAxisResult,
  CriticVerdictReady,
  CriticVerdict,
} from "@/types/missions";
import { useMissionsStore } from "./store";

const KNOWN_AXES = ["correctness", "completeness", "side_effects", "security"];

const VERDICT_STYLE: Record<CriticVerdict, { className: string; label: string }> = {
  approve: {
    className: "border-emerald-400/40 bg-emerald-400/10 text-emerald-300",
    label: "approve",
  },
  revise: {
    className: "border-amber-400/40 bg-amber-400/10 text-amber-300",
    label: "revise",
  },
  reject: {
    className: "border-destructive/50 bg-destructive/15 text-destructive",
    label: "reject",
  },
};

export function VerdictPanel() {
  const verdicts = useMissionsStore(
    useShallow((s) => {
      if (!s.selectedMissionId) return [] as CriticVerdictReady[];
      return s.verdictsByMission[s.selectedMissionId] ?? [];
    }),
  );

  if (verdicts.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-xs text-muted-foreground">
        <ListChecks className="h-7 w-7 text-muted-foreground/50" />
        <p>Noch keine Critic-Verdicts fuer diese Mission.</p>
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-3 p-3">
        {verdicts.map((v, idx) => (
          <VerdictCard
            key={`${v.worker_id}-${v.iteration}-${idx}`}
            verdict={v}
          />
        ))}
      </div>
    </ScrollArea>
  );
}

function VerdictCard({ verdict }: { verdict: CriticVerdictReady }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const style = VERDICT_STYLE[verdict.verdict];
  const axes = mergeAxes(verdict.axes);

  return (
    <article className="card-outline overflow-hidden">
      <header className="flex items-start gap-3 border-b border-border bg-card/40 px-3 py-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "rounded px-1.5 py-0.5 text-[10px] font-mono uppercase tracking-wider",
                style.className,
              )}
            >
              {style.label}
            </span>
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              iter #{verdict.iteration}
            </span>
            <span className="font-mono text-[10px] text-muted-foreground">
              w{verdict.worker_id.slice(0, 8)}
            </span>
          </div>
          <p className="mt-1 line-clamp-3 text-xs text-foreground/90">
            {verdict.summary}
          </p>
        </div>
      </header>
      <div className="px-3 py-2">
        <ConfidenceBar value={verdict.confidence} />
      </div>
      <ul className="space-y-1 border-t border-border bg-background/30 p-2">
        {axes.map(({ name, axis }) => {
          const isOpen = !!expanded[name];
          const evidence = (axis.evidence ?? []).filter((e) => typeof e === "string");
          const Icon = axis.pass === true
            ? CheckCircle2
            : axis.pass === false
            ? XCircle
            : name === "security"
            ? ShieldAlert
            : Shield;
          const tone = axis.pass === true
            ? "text-emerald-300"
            : axis.pass === false
            ? "text-destructive"
            : "text-muted-foreground";
          return (
            <li key={name}>
              <button
                type="button"
                onClick={() => setExpanded((p) => ({ ...p, [name]: !p[name] }))}
                className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs hover:bg-background/60"
              >
                {evidence.length > 0 ? (
                  isOpen ? (
                    <ChevronDown className="h-3 w-3 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="h-3 w-3 text-muted-foreground" />
                  )
                ) : (
                  <span className="w-3" />
                )}
                <Icon className={cn("h-3.5 w-3.5", tone)} />
                <span className="flex-1 font-mono text-[11px]">{name}</span>
                <span className={cn("text-[10px] uppercase tracking-wider", tone)}>
                  {axis.pass === true
                    ? "pass"
                    : axis.pass === false
                    ? "fail"
                    : "—"}
                </span>
              </button>
              {isOpen && evidence.length > 0 && (
                <ul className="ml-7 mt-1 space-y-0.5 border-l border-border pl-2">
                  {evidence.map((e, i) => (
                    <li
                      key={i}
                      className="text-[10px] font-mono text-muted-foreground break-all"
                    >
                      • {String(e)}
                    </li>
                  ))}
                  {axis.notes && (
                    <li className="text-[10px] text-muted-foreground italic">
                      {axis.notes}
                    </li>
                  )}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </article>
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-[10px] uppercase tracking-wider text-muted-foreground">
        <span>Confidence</span>
        <span className="font-mono">{pct.toFixed(0)}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-background/60">
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function mergeAxes(
  raw: Record<string, CriticAxisResult>,
): Array<{ name: string; axis: CriticAxisResult }> {
  const out: Array<{ name: string; axis: CriticAxisResult }> = [];
  const seen = new Set<string>();
  for (const known of KNOWN_AXES) {
    out.push({ name: known, axis: raw[known] ?? {} });
    seen.add(known);
  }
  for (const [name, axis] of Object.entries(raw)) {
    if (!seen.has(name)) out.push({ name, axis });
  }
  return out;
}
