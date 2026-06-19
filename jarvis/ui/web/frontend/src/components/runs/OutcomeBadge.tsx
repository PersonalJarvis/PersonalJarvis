// Functional run/turn outcome — distinct from SLO latency. Disciplined colors:
// emerald = success, amber = partial (hiccup but answered), rose = genuine failure.
// An unknown value degrades to a neutral style (BUG-008 string contract).

type OutcomeStyle = { label: string; dot: string; badge: string };

const OUTCOME_STYLE: Record<string, OutcomeStyle> = {
  success: {
    label: "Success",
    dot: "bg-emerald-400",
    badge: "bg-emerald-400/10 text-emerald-300 ring-emerald-400/25",
  },
  partial: {
    label: "Partial",
    dot: "bg-amber-400",
    badge: "bg-amber-400/10 text-amber-300 ring-amber-400/25",
  },
  failed: {
    label: "Failed",
    dot: "bg-rose-500",
    badge: "bg-rose-500/10 text-rose-300 ring-rose-500/25",
  },
};

const FALLBACK: OutcomeStyle = {
  label: "—",
  dot: "bg-muted-foreground/40",
  badge: "bg-muted/40 text-muted-foreground ring-border",
};

export function outcomeStyle(outcome: string): OutcomeStyle {
  return OUTCOME_STYLE[outcome] ?? FALLBACK;
}

export function OutcomeDot({ outcome, className = "" }: { outcome: string; className?: string }) {
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
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${s.badge}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}
