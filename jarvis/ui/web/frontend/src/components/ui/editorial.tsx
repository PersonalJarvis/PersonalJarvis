import { ArrowLeft, ArrowRight, Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { Button, SectionLabel } from "@/components/agentic/controls";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * Editorial building blocks in the workspace launcher's language: hairlines
 * and type do the structuring, numbers sit in a mono register, and there is
 * one primary action at the foot. Used by setup cards that read as a short
 * document rather than a dialog (the local-models setup proposal).
 */

/** A labelled block: eyebrow on top, hairline-separated content below. */
export function StepSection({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("space-y-3", className)}>
      <SectionLabel>{label}</SectionLabel>
      {children}
    </section>
  );
}

/**
 * A numbered register — the same `01 / 02` list the launcher uses for its
 * terminal plan. Rows are separated by rules, never boxed.
 */
export function Register({
  items,
  className,
}: {
  items: { key: string; icon?: ReactNode; children: ReactNode }[];
  className?: string;
}) {
  return (
    <ol className={cn("border-y border-border/70", className)}>
      {items.map((item, index) => (
        <li
          key={item.key}
          className="grid grid-cols-[2.25rem_minmax(0,1fr)] items-baseline gap-2 border-b border-border/50 py-3 text-lg last:border-b-0"
        >
          <span className="font-mono text-xs tabular-nums text-muted-foreground/70">
            {(index + 1).toString().padStart(2, "0")}
          </span>
          <span className="flex min-w-0 items-start gap-2.5">
            {item.icon ? (
              <span className="mt-0.5 shrink-0 text-muted-foreground" aria-hidden>
                {item.icon}
              </span>
            ) : null}
            <span className="min-w-0 leading-relaxed">{item.children}</span>
          </span>
        </li>
      ))}
    </ol>
  );
}

export interface FooterAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
  testId?: string;
}

/**
 * The step footer: a rule, Back on the left, the one primary action on the
 * right, with an optional quiet secondary beside it. Every step ends here so
 * the eye always knows where "go on" lives.
 */
export function StepFooter({
  onBack,
  primary,
  secondary,
  backLabel,
  hidePrimaryArrow,
}: {
  onBack?: (() => void) | null;
  primary: FooterAction;
  secondary?: FooterAction | null;
  backLabel?: string;
  hidePrimaryArrow?: boolean;
}) {
  const t = useT();
  return (
    <footer className="mt-10 flex min-h-10 flex-wrap items-center justify-between gap-3 border-t border-border/70 pt-6">
      {onBack ? (
        <Button variant="subtle" onClick={onBack} data-testid="editorial-back">
          <ArrowLeft className="h-3.5 w-3.5" />
          {backLabel ?? t("common.back")}
        </Button>
      ) : (
        <span />
      )}
      <div className="flex flex-wrap items-center gap-2">
        {secondary ? (
          <Button
            variant="subtle"
            onClick={secondary.onClick}
            disabled={secondary.disabled || secondary.busy}
            data-testid={secondary.testId}
          >
            {secondary.busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {secondary.label}
          </Button>
        ) : null}
        <Button
          variant="primary"
          className="h-10 min-w-40 px-5 text-lg"
          onClick={primary.onClick}
          disabled={primary.disabled || primary.busy}
          data-testid={primary.testId ?? "editorial-primary"}
        >
          {primary.busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {primary.label}
          {!primary.busy && !hidePrimaryArrow && <ArrowRight className="h-3.5 w-3.5" />}
        </Button>
      </div>
    </footer>
  );
}
