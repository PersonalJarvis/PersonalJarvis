/**
 * The functional outcome of a run or a turn — distinct from SLO latency.
 *
 * The ramp used to be inverted: "success" drew the same grey dot as an unknown
 * value, "partial" was painted in --foreground and so became the loudest mark
 * on the screen, and "failed" reached for a literal rose. Status is the only
 * place hue is allowed in this product, and it has exactly three jobs — life,
 * degraded, fault. So the dot carries the hue, the chip stays on the neutral
 * lift every other small surface uses, and the unknown value is the ONE that
 * recedes (--faint-foreground), never the successful one.
 *
 * An unknown value still degrades to a neutral style rather than throwing
 * (BUG-008 string contract).
 */

type OutcomeStyle = { label: string; dot: string };

const OUTCOME_STYLE: Record<string, OutcomeStyle> = {
  success: { label: "Success", dot: "bg-success" },
  partial: { label: "Partial", dot: "bg-warning" },
  failed: { label: "Failed", dot: "bg-destructive" },
};

const FALLBACK: OutcomeStyle = { label: "—", dot: "bg-faint-foreground" };

export function outcomeStyle(outcome: string): OutcomeStyle {
  return OUTCOME_STYLE[outcome] ?? FALLBACK;
}

export function OutcomeDot({
  outcome,
  className = "",
}: {
  outcome: string;
  className?: string;
}) {
  return (
    <span
      data-outcome={outcome}
      className={`inline-block h-2 w-2 shrink-0 rounded-full ${outcomeStyle(outcome).dot} ${className}`}
    />
  );
}

export function OutcomeBadge({ outcome }: { outcome: string }) {
  const s = outcomeStyle(outcome);
  return (
    <span
      data-outcome={outcome}
      className="inline-flex items-center gap-1.5 rounded-full bg-secondary px-2.5 py-0.5 text-micro font-medium text-foreground"
    >
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}
