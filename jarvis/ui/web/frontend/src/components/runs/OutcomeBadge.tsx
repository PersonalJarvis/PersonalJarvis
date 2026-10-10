import { Badge } from "@/components/ui/badge";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The functional outcome of a run or a turn — distinct from SLO latency.
 *
 * Status is the only place hue is allowed in this product, and it has exactly
 * three jobs: life, degraded, fault. The badge is the shared soft wash
 * (`success` / `warning` / `destructive`), the dot is the same hue at 8 px,
 * and an unrecorded outcome is the ONE that recedes into the neutral outline —
 * never the successful one.
 *
 * An unknown value still degrades to a neutral style rather than throwing
 * (BUG-008 string contract).
 */

type BadgeTone = "success" | "warning" | "destructive" | "outline";

/** `label` is an i18n key (or a literal glyph for the fallback). */
type OutcomeStyle = { label: string; dot: string; tone: BadgeTone };

const OUTCOME_STYLE: Record<string, OutcomeStyle> = {
  success: { label: "run_inspector.outcome.success", dot: "bg-success", tone: "success" },
  partial: { label: "run_inspector.outcome.partial", dot: "bg-warning", tone: "warning" },
  failed: { label: "run_inspector.outcome.failed", dot: "bg-destructive", tone: "destructive" },
};

const FALLBACK: OutcomeStyle = {
  label: "—",
  dot: "bg-foreground-faint",
  tone: "outline",
};

export function outcomeStyle(outcome: string): OutcomeStyle {
  return OUTCOME_STYLE[outcome] ?? FALLBACK;
}

export function OutcomeDot({
  outcome,
  className,
}: {
  outcome: string;
  className?: string;
}) {
  return (
    <span
      data-outcome={outcome}
      aria-hidden
      className={cn(
        "inline-block h-2 w-2 shrink-0 rounded-full",
        outcomeStyle(outcome).dot,
        className,
      )}
    />
  );
}

export function OutcomeBadge({ outcome }: { outcome: string }) {
  const s = outcomeStyle(outcome);
  const t = useT();
  return (
    <Badge variant={s.tone} data-outcome={outcome}>
      <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", s.dot)} aria-hidden />
      {s.label === FALLBACK.label ? s.label : t(s.label)}
    </Badge>
  );
}
