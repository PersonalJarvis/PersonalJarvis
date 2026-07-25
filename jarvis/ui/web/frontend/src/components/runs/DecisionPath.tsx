/**
 * Why this turn went the way it did — one row per recorded decision.
 *
 * The backend has carried a `rationale` + its provenance since the
 * Session-Decision-Log, but the UI only ever rendered the terse label, so the
 * honest "why" was invisible. The provenance tag matters: "model" is the
 * brain's OWN words captured next to its tool call, "rule" is a deterministic
 * explanation derived from a recorded fact. Neither is ever invented — a step
 * with no recorded rationale says so instead of guessing.
 */
import { useT } from "@/i18n";

import type { DecisionStep } from "./types";

const KIND_META: Record<string, { icon: string; cls: string }> = {
  tier: { icon: "◆", cls: "text-slate-300" },
  route: { icon: "→", cls: "text-amber-300" },
  risk: { icon: "⚖", cls: "text-rose-300" },
  brain: { icon: "🧠", cls: "text-violet-300" },
  mission: { icon: "⚙", cls: "text-fuchsia-300" },
  fallback: { icon: "↺", cls: "text-cyan-300" },
};

const SOURCE_STYLE: Record<string, string> = {
  model: "bg-violet-400/10 text-violet-300 ring-violet-400/25",
  rule: "bg-muted/40 text-muted-foreground ring-border",
};

export function DecisionPath({ steps }: { steps: DecisionStep[] }) {
  const t = useT();
  if (steps.length === 0) {
    return <span className="text-muted-foreground/60">{t("run_inspector.decision.empty")}</span>;
  }
  return (
    <ol className="space-y-1.5" data-testid="decision-path">
      {steps.map((s, i) => {
        const meta = KIND_META[s.kind] ?? { icon: "·", cls: "text-muted-foreground" };
        return (
          <li
            key={i}
            data-decision-kind={s.kind}
            className="rounded-md border border-border/50 bg-background/40 px-2 py-1.5"
          >
            <div className="flex flex-wrap items-baseline gap-1.5 text-[11px]">
              <span className={`w-4 shrink-0 text-center ${meta.cls}`}>{meta.icon}</span>
              <span className="font-medium text-foreground/90">{s.label}</span>
              {s.detail && (
                <span className="font-mono text-[10px] text-muted-foreground">{s.detail}</span>
              )}
              <span
                className={`ml-auto rounded-full px-1.5 py-px text-[9px] uppercase tracking-wide ring-1 ring-inset ${SOURCE_STYLE[s.rationale_source] ?? SOURCE_STYLE.rule}`}
              >
                {s.rationale_source || t("run_inspector.decision.no_source")}
              </span>
            </div>
            <p className="mt-1 pl-[22px] text-[11px] leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">
              {s.rationale || t("run_inspector.decision.no_rationale")}
            </p>
          </li>
        );
      })}
    </ol>
  );
}
