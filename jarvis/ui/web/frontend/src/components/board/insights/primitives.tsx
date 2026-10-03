import type { ReactNode } from "react";
import { Info } from "lucide-react";

import { QuickTooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** The Board's one card: a quiet object on the room, padding on the root. */
export function InsightCard({
  className,
  children,
  testId,
}: {
  className?: string;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <section
      data-testid={testId}
      className={cn("flex min-w-0 flex-col rounded-lg border border-border bg-card p-5", className)}
    >
      {children}
    </section>
  );
}

/** Small uppercase caption under a headline number, with an optional hint. */
export function Eyebrow({ children, hint }: { children: ReactNode; hint?: string }) {
  return (
    <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">
      <span className="truncate">{children}</span>
      {hint && (
        <QuickTooltip content={hint} side="top">
          <Info className="h-3.5 w-3.5 text-foreground-faint" aria-label={hint} />
        </QuickTooltip>
      )}
    </div>
  );
}

export function BigNumber({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "text-2xl font-semibold tabular-nums leading-none text-foreground-strong",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** Card title row: a heading on the left, an uppercase fact on the right. */
export function CardTitleRow({
  title,
  fact,
  children,
}: {
  title: ReactNode;
  fact?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
      <div className="min-w-0">
        <h2 className="text-xl font-semibold text-foreground-strong">{title}</h2>
        {children}
      </div>
      {fact && (
        <div className="pt-1.5 text-xs font-medium uppercase tracking-[0.08em] text-foreground tabular-nums">
          {fact}
        </div>
      )}
    </div>
  );
}

export function Hairline() {
  return <div className="my-4 h-px bg-border" />;
}

/** A loading placeholder — never a fallback number (see loading states). */
export function SkeletonBlock({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-foreground/10", className)} />;
}

/** Heat colour for an intensity level, from the one accent hue. */
export const HEAT_LEVEL_CLASS: Record<0 | 1 | 2 | 3 | 4, string> = {
  0: "bg-sheen/[0.07]",
  1: "bg-accent/25",
  2: "bg-accent/45",
  3: "bg-accent/70",
  4: "bg-accent",
};
