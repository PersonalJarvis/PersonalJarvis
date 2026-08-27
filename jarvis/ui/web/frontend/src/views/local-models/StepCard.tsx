/**
 * A numbered step card — the shape the Local models overview is built from.
 *
 * The section answers one question in three moves: is there a server, are
 * there models, and which model does which job. Written as a dashboard that
 * reads as a wall of equal tiles, a newcomer has no idea where to start; so
 * each move is one card that carries its own ordinal, its own state and at
 * most one primary action. The ordinal turns into a check once the step is
 * satisfied, which makes "what is still missing" a glance rather than a read.
 *
 * `state` drives the ordinal only. A card is never hidden or disabled by it:
 * step 3 stays open while step 1 is unsatisfied, because someone reinstalling
 * a server still wants to see which model each job is on.
 */
import type { ReactNode } from "react";
import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

export type StepState = "done" | "attention" | "todo" | "busy";

export interface StepCardProps {
  /** 1-based ordinal shown in the marker; replaced by a check when done. */
  step: number;
  title: string;
  /** One plain sentence saying what this step is for. */
  subtitle: string;
  state: StepState;
  /** The step's status line, right of the title on a wide pane. */
  status?: ReactNode;
  /** The one primary action, rendered in the header. */
  action?: ReactNode;
  children: ReactNode;
  testId?: string;
}

const MARKER: Record<StepState, string> = {
  // Emerald carries "finished cleanly" here exactly as it does in StatTile —
  // the token set has no name for it, and it needs its own value per theme.
  done: "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  attention: "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  busy: "border-primary/40 bg-primary/10 text-primary",
  todo: "border-border bg-sheen/[0.06] text-muted-foreground",
};

export function StepCard({
  step,
  title,
  subtitle,
  state,
  status,
  action,
  children,
  testId,
}: StepCardProps) {
  return (
    <section
      className="overflow-hidden rounded-xl border border-border bg-card/50"
      aria-label={title}
      data-testid={testId}
      data-step={step}
      data-state={state}
    >
      <header className="flex flex-wrap items-start gap-x-4 gap-y-3 border-b border-border/70 px-4 py-3.5">
        <span
          aria-hidden
          className={cn(
            "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border",
            "font-display text-[13px] font-semibold tabular-nums",
            MARKER[state],
          )}
        >
          {state === "done" ? <Check className="h-3.5 w-3.5" /> : step}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-[15px] font-semibold tracking-tight text-foreground">
            {title}
          </h3>
          <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>
          {status ? <div className="mt-1.5 text-sm">{status}</div> : null}
        </div>
        {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
      </header>
      <div className="px-4 py-3.5">{children}</div>
    </section>
  );
}
