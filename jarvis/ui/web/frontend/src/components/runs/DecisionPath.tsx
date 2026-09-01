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
 * The six kinds are told apart by their GLYPH, not by six hues: a decision
 * kind is neither a status nor an identity, and painting `risk` rose made
 * every routing trace look like it contained an error.
 */
import { useT } from "@/i18n";

import type { DecisionStep } from "./types";

const KIND_ICON: Record<string, string> = {
  tier: "◆",
  route: "→",
  risk: "⚖",
  brain: "🧠",
  mission: "⚙",
  fallback: "↺",
};

export function DecisionPath({ steps }: { steps: DecisionStep[] }) {
  const t = useT();
  if (steps.length === 0) {
    return (
      <span className="text-body text-muted-foreground">
        {t("run_inspector.decision.empty")}
      </span>
    );
  }
  return (
    <ol className="space-y-1" data-testid="decision-path">
      {steps.map((s, i) => (
        <li
          key={i}
          data-decision-kind={s.kind}
          className="rounded-md px-2 py-1.5 transition-colors hover:bg-secondary"
        >
          <div className="flex flex-wrap items-baseline gap-1.5 text-body">
            <span className="w-4 shrink-0 text-center text-muted-foreground">
              {KIND_ICON[s.kind] ?? "·"}
            </span>
            <span className="font-medium text-foreground">{s.label}</span>
            {s.detail && (
              <span className="font-mono text-micro text-muted-foreground">
                {s.detail}
              </span>
            )}
            <span className="ml-auto rounded-full bg-secondary px-2 py-px text-micro text-muted-foreground">
              {s.rationale_source || t("run_inspector.decision.no_source")}
            </span>
          </div>
          <p className="mt-1 pl-[22px] text-meta text-muted-foreground [overflow-wrap:anywhere]">
            {s.rationale || t("run_inspector.decision.no_rationale")}
          </p>
        </li>
      ))}
    </ol>
  );
}
