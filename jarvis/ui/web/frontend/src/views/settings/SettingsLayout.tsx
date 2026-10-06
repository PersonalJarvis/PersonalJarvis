import type { ReactNode } from "react";

import { BrandedSelect, type BrandedSelectOption } from "@/components/ui/select";
import { cn } from "@/lib/utils";

/*
 * The building blocks of the General settings page.
 *
 * One reading column, a title, then sections: a short heading above one
 * grouped card whose rows are split by hairlines. Every row has the same
 * shape — the setting's name and one quiet sentence on the left, its control
 * on the right — so the page scans as a list of decisions instead of a stack
 * of separately styled panels. Extra content a row needs (a slider, a status
 * line, a note) sits under the row's text at the row's own inset.
 */

/** The page: a centred reading column with a title. */
export function SettingsPage({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto w-full max-w-3xl px-6 pb-20 pt-10 sm:px-10">
      <header className="mb-8">
        <h1 className="font-display text-2xl font-semibold text-foreground-strong">{title}</h1>
        {description && <p className="mt-1.5 text-base text-muted-foreground">{description}</p>}
      </header>
      <div className="space-y-10">{children}</div>
    </div>
  );
}

/** A heading with an optional line under it, then the section's card(s). */
export function SettingsSection({
  title,
  description,
  actions,
  children,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  /** Right-aligned beside the heading — a rescan button, a badge. */
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("space-y-3", className)}>
      <div className="flex items-end justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-foreground-strong">{title}</h2>
          {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

/** The grouped surface: rows stacked with a hairline between each. */
export function SettingsCard({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "divide-y divide-border rounded-lg border border-border bg-card",
        className,
      )}
    >
      {children}
    </div>
  );
}

/**
 * One setting: name + description on the left, the control on the right.
 * `children` render under the text at full row width.
 */
export function SettingsRow({
  title,
  description,
  control,
  children,
  id,
  testId,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  control?: ReactNode;
  children?: ReactNode;
  id?: string;
  testId?: string;
  className?: string;
}) {
  return (
    <div id={id} data-testid={testId} className={cn("scroll-mt-6 px-4 py-3.5", className)}>
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
        <div className="min-w-0 flex-1 basis-64">
          <div className="text-base font-medium text-foreground">{title}</div>
          {description && (
            <div className="mt-0.5 text-sm text-muted-foreground">{description}</div>
          )}
        </div>
        {control && <div className="flex shrink-0 items-center gap-2">{control}</div>}
      </div>
      {children && <div className="mt-3 space-y-2">{children}</div>}
    </div>
  );
}

/** A quiet lifted block inside a row: a hint, a result, a follow-up action. */
export function SettingsNote({
  children,
  tone = "default",
  className,
  testId,
}: {
  children: ReactNode;
  tone?: "default" | "success" | "warning" | "error";
  className?: string;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      className={cn(
        "rounded-md bg-secondary px-3 py-2.5 text-sm",
        tone === "default" && "text-foreground",
        tone === "success" && "text-success",
        tone === "warning" && "text-warning",
        tone === "error" && "text-destructive",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** A compact dropdown that sits at the right edge of a row. */
export function SettingsSelect({
  value,
  options,
  onValueChange,
  ariaLabel,
  placeholder,
  disabled,
  testId,
  className,
}: {
  value: string;
  options: readonly BrandedSelectOption[];
  onValueChange: (value: string) => void;
  ariaLabel: string;
  placeholder?: string;
  disabled?: boolean;
  testId?: string;
  className?: string;
}) {
  return (
    <BrandedSelect
      value={value}
      options={options}
      onValueChange={onValueChange}
      ariaLabel={ariaLabel}
      placeholder={placeholder}
      disabled={disabled}
      testId={testId}
      className={cn(settingsSelectCls, className)}
    />
  );
}

/** Trigger styling shared by every right-aligned dropdown on the page. */
export const settingsSelectCls =
  "h-8 w-auto max-w-[16rem] gap-1.5 bg-transparent py-0 pl-2.5 pr-2 hover:bg-secondary";

/** Two to four exclusive choices drawn side by side. */
export function SettingsSegmented<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  disabled,
}: {
  value: T;
  options: readonly { value: T; label: string; icon?: ReactNode }[];
  onChange: (value: T) => void;
  ariaLabel: string;
  disabled?: boolean;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className="inline-flex items-center gap-0.5 rounded-md border border-border p-0.5"
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={cn(
              "inline-flex h-7 items-center gap-1.5 rounded px-2.5 text-sm font-medium transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
              "[&>svg]:h-3.5 [&>svg]:w-3.5",
              active
                ? "bg-secondary text-foreground-strong"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {option.icon}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/** A range input in the page's one accent, with a readable track. */
export const settingsRangeCls =
  "h-1.5 w-full cursor-pointer accent-accent disabled:cursor-not-allowed disabled:opacity-50";

/** The value read-out beside a slider row's title. */
export function SettingsValue({ children }: { children: ReactNode }) {
  return (
    <span className="min-w-[3.5rem] text-right text-sm font-medium tabular-nums text-foreground">
      {children}
    </span>
  );
}

/** A small text action under a row ("Reset to default"). */
export function SettingsLinkButton({
  children,
  onClick,
  disabled,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="text-sm text-muted-foreground underline-offset-4 transition-colors hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
    >
      {children}
    </button>
  );
}
