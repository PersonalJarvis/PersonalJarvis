import { useState, type ComponentPropsWithoutRef, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * The provider page's visual grammar: type and hairlines, no boxes.
 *
 * A section is a heading with one quiet sentence. Its rows sit on the page
 * itself, separated by hairlines that run the width of the column — the
 * structure comes from alignment and rhythm, never from a card around a card.
 * Controls are small and sit on the right edge of their row.
 */
export function Section({
  title,
  description,
  action,
  children,
  className,
  ...props
}: Omit<ComponentPropsWithoutRef<"section">, "title"> & {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section {...props} className={cn("min-w-0", className)}>
      <header className="flex items-end justify-between gap-6 pb-3">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-foreground-strong">{title}</h2>
          {description ? <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{description}</p> : null}
        </div>
        {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
      </header>
      {children}
    </section>
  );
}

/** Rows divided by hairlines, with a hairline above the first. */
export function Rows({ className, ...props }: ComponentPropsWithoutRef<"div">) {
  return <div {...props} className={cn("divide-y divide-border/60 border-t border-border/60", className)} />;
}

/** One setting: what it is on the left, its control on the right. */
export function Row({
  title,
  description,
  control,
  children,
  stacked = false,
  className,
  ...props
}: Omit<ComponentPropsWithoutRef<"div">, "title"> & {
  title: ReactNode;
  description?: ReactNode;
  control?: ReactNode;
  /** Content under the row, full width (an account picker, a list of chips). */
  children?: ReactNode;
  /** Control under the text instead of beside it (narrow columns). */
  stacked?: boolean;
}) {
  return (
    <div {...props} className={cn("py-4", className)}>
      <div
        className={cn(
          "flex flex-col gap-3",
          !stacked && "sm:flex-row sm:items-center sm:justify-between sm:gap-8",
        )}
      >
        <div className="min-w-0">
          <div className="text-sm font-medium text-foreground">{title}</div>
          {description ? <div className="mt-0.5 max-w-xl text-sm text-muted-foreground">{description}</div> : null}
        </div>
        {control ? <div className="flex min-w-0 shrink-0 flex-wrap items-center gap-2">{control}</div> : null}
      </div>
      {children ? <div className="pt-3">{children}</div> : null}
    </div>
  );
}

/** Text tabs on a hairline, the selected one underlined. */
export function UnderlineTabs<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} data-testid="api-keys-category-tabs" className="flex gap-7 border-b border-border/60">
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="tab"
            id={`apikeys-tab-${option.value}`}
            aria-selected={selected}
            aria-controls="apikeys-panel"
            onClick={() => onChange(option.value)}
            className={cn(
              "-mb-px h-10 border-b-2 text-sm font-medium transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              selected
                ? "border-foreground text-foreground-strong"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/** A small two- or three-way choice, as a pill track. */
export function Choice<T extends string>({
  value,
  options,
  onChange,
  label,
  disabled,
  testId,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
  disabled?: boolean;
  testId?: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} data-testid={testId} className="inline-flex rounded-full bg-secondary/70 p-0.5">
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            onClick={() => !selected && onChange(option.value)}
            className={cn(
              "h-7 rounded-full px-3 text-xs font-medium transition-colors disabled:opacity-50",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              selected ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/** A model (or any item) that is on or off, as a chip that toggles on click. */
export function ToggleChip({
  on,
  label,
  hint,
  disabled,
  onToggle,
}: {
  on: boolean;
  label: string;
  hint?: string;
  disabled?: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      title={hint}
      disabled={disabled}
      onClick={onToggle}
      className={cn(
        "inline-flex h-7 max-w-[16rem] items-center gap-1.5 rounded-full border px-3 text-xs transition-colors disabled:opacity-50",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        on
          ? "border-accent/50 bg-accent-soft text-foreground"
          : "border-border/70 text-muted-foreground hover:text-foreground",
      )}
    >
      <span
        aria-hidden="true"
        className={cn("h-1.5 w-1.5 shrink-0 rounded-full", on ? "bg-accent" : "bg-border-strong")}
      />
      <span className="truncate">{label}</span>
    </button>
  );
}

/** Rarely needed settings, folded behind one quiet line. */
export function Disclosure({
  label,
  children,
  defaultOpen = false,
  testId,
}: {
  label: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  testId?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div data-testid={testId}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex items-center gap-1.5 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ChevronRight aria-hidden="true" className={cn("h-4 w-4 transition-transform motion-reduce:transition-none", open && "rotate-90")} />
        {label}
      </button>
      {open ? <div className="pt-2">{children}</div> : null}
    </div>
  );
}

/** A status line: a small dot and a few words. */
export function StatusLine({ tone, children }: { tone: "ok" | "off" | "warn" | "error"; children: ReactNode }) {
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 text-sm text-muted-foreground">
      <span
        aria-hidden="true"
        className={cn(
          "h-1.5 w-1.5 shrink-0 rounded-full",
          tone === "ok" && "bg-success",
          tone === "off" && "bg-border-strong",
          tone === "warn" && "bg-warning",
          tone === "error" && "bg-destructive",
        )}
      />
      <span className="truncate">{children}</span>
    </span>
  );
}
