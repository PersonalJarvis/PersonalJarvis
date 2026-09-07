/**
 * Why this turn went the way it did — one row per recorded decision.
 *
 * The backend has carried a `rationale` + its provenance since the
 * Session-Decision-Log, but the UI only ever rendered the terse label, so the
 * honest "why" was invisible. The provenance tag matters: "model" is the
 * brain's OWN words captured next to its tool call, "rule" is a deterministic
 * explanation derived from a recorded fact. Neither is ever invented — a step
 * with no recorded rationale says so instead of guessing.
 *
 * A timeline, so it reads top-down and left-aligned: the glyph column is the
 * spine, the rationale hangs under the label it explains. The six kinds are
 * told apart by their GLYPH, never by six hues — a decision kind is neither a
 * status nor an identity.
 */
import type { LucideIcon } from "lucide-react";
import { Brain, CircleDot, Layers, RotateCcw, Route, Scale, Workflow } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useT } from "@/i18n";

import type { DecisionStep } from "./types";

const KIND_ICON: Record<string, LucideIcon> = {
  tier: Layers,
  route: Route,
  risk: Scale,
  brain: Brain,
  mission: Workflow,
  fallback: RotateCcw,
};

export function DecisionPath({ steps }: { steps: DecisionStep[] }) {
  const t = useT();
  if (steps.length === 0) {
    return (
      <p className="text-base text-muted-foreground">
        {t("run_inspector.decision.empty")}
      </p>
    );
  }
  return (
    <ol className="space-y-0.5" data-testid="decision-path">
      {steps.map((s, i) => {
        const Icon = KIND_ICON[s.kind] ?? CircleDot;
        const last = i === steps.length - 1;
        return (
          <li
            key={i}
            data-decision-kind={s.kind}
            className="grid grid-cols-[20px_minmax(0,1fr)] gap-x-3"
          >
            {/* The spine: glyph, then a hairline down to the next step. */}
            <div className="flex flex-col items-center">
              <Icon aria-hidden className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />
              {!last && <span aria-hidden className="w-px flex-1 bg-border" />}
            </div>
            <div className="min-w-0 pb-3">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="text-base font-medium text-foreground-strong">
                  {s.label}
                </span>
                {s.detail && (
                  <span className="font-mono text-sm text-muted-foreground">
                    {s.detail}
                  </span>
                )}
                <Badge variant="outline" className="ml-auto shrink-0">
                  {s.rationale_source || t("run_inspector.decision.no_source")}
                </Badge>
              </div>
              <p className="mt-1 text-sm text-muted-foreground [overflow-wrap:anywhere]">
                {s.rationale || t("run_inspector.decision.no_rationale")}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
