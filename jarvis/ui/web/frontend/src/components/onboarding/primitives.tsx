import { ArrowLeft, ArrowRight, Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { Button, FOCUS_RING, SectionLabel } from "@/components/agentic/controls";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The first-run guide speaks the workspace launcher's language: hairlines and
 * type do the structuring, numbers sit in a mono register, and yellow is
 * reserved for the choice you made and the one action that moves you on.
 * These are the handful of shapes every step is built from, so the six
 * screens read as one document rather than six dialogs.
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
          className="grid grid-cols-[2.25rem_minmax(0,1fr)] items-baseline gap-2 border-b border-border/50 py-3 text-[15px] last:border-b-0"
        >
          <span className="font-mono text-[11px] tabular-nums text-muted-foreground/70">
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

/**
 * A selectable row. Selection is a gold edge and a faint gold wash plus a
 * small "SELECTED" tag — the launcher's card-selection idiom — so the state
 * is legible without filling the whole row.
 */
export function ChoiceRow({
  selected,
  title,
  body,
  onSelect,
  testId,
  badge,
  meta,
}: {
  selected: boolean;
  title: string;
  body: string;
  onSelect: () => void;
  testId?: string;
  /** A short gold tag after the title ("Recommended"). */
  badge?: string | null;
  /** Small mono facts on the right ("1 key · Pipeline"). */
  meta?: ReactNode;
}) {
  const t = useT();
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      data-testid={testId}
      onClick={onSelect}
      className={cn(
        "flex w-full items-start justify-between gap-4 rounded-control border px-5 py-4 text-left transition-colors",
        FOCUS_RING,
        selected
          ? "border-primary/70 bg-primary/[0.04]"
          : "border-border/70 hover:border-border hover:bg-secondary/40",
      )}
    >
      <span className="min-w-0">
        <span className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
          <span className="text-base font-medium text-foreground">{title}</span>
          {badge ? (
            <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-primary">
              {badge}
            </span>
          ) : null}
        </span>
        <span className="mt-1 block text-sm leading-relaxed text-muted-foreground">{body}</span>
      </span>
      <span className="flex shrink-0 flex-col items-end gap-1.5 pt-0.5">
        {selected ? (
          <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-primary">
            {t("onboarding.wake_word.selected")}
          </span>
        ) : null}
        {meta ? (
          <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
            {meta}
          </span>
        ) : null}
      </span>
    </button>
  );
}

/** A checkbox that reads as one line of a document, not a form control. */
export function ConsentLine({
  checked,
  onChange,
  children,
  testId,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 py-1 text-[15px] leading-relaxed">
      <input
        type="checkbox"
        data-testid={testId}
        className={cn("mt-1 h-4 w-4 shrink-0 accent-primary", FOCUS_RING)}
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{children}</span>
    </label>
  );
}

/**
 * One tone-carrying line: a coloured left edge, no fill, no icon — the
 * launcher's `Notice`, with a success tone added because a saved key or a
 * clear microphone are things the guide wants to confirm in place.
 */
export function StatusLine({
  tone,
  children,
  testId,
}: {
  tone: "ok" | "warning" | "error" | "muted";
  children: ReactNode;
  testId?: string;
}) {
  return (
    <p
      data-testid={testId}
      className={cn(
        "border-l-2 py-1 pl-3 text-sm leading-relaxed",
        tone === "ok" && "border-muted-foreground/70 text-muted-foreground",
        tone === "warning" && "border-foreground/70 text-foreground",
        tone === "error" && "border-destructive/70 text-destructive",
        tone === "muted" && "border-border text-muted-foreground",
      )}
    >
      {children}
    </p>
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
        <Button variant="subtle" onClick={onBack} data-testid="onboarding-back">
          <ArrowLeft className="h-3.5 w-3.5" />
          {backLabel ?? t("onboarding.nav.back")}
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
          className="h-10 min-w-40 px-5 text-[15px]"
          onClick={primary.onClick}
          disabled={primary.disabled || primary.busy}
          data-testid={primary.testId ?? "onboarding-primary"}
        >
          {primary.busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {primary.label}
          {!primary.busy && !hidePrimaryArrow && <ArrowRight className="h-3.5 w-3.5" />}
        </Button>
      </div>
    </footer>
  );
}
