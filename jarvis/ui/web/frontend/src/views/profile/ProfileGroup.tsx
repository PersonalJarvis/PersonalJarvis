/**
 * The two layout primitives every part of the Profile page is built from.
 *
 * `ProfileGroup` is a titled block: a heading and one plain sentence above a
 * single bordered list. `SettingRow` is one line inside it: a label and an
 * explanation on the left, the value or the action on the right. The same
 * row grammar the rest of the Settings hub uses (Appshots, Pets), so the
 * profile reads as part of the product rather than as a dashboard of cards.
 */
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function ProfileGroup({
  title,
  description,
  aside,
  children,
  testId,
  className,
  bare = false,
}: {
  title: string;
  description?: string;
  /** Right-aligned beside the heading — a count or one quiet action. */
  aside?: ReactNode;
  children: ReactNode;
  testId?: string;
  className?: string;
  /** Drop the visible heading when a surrounding panel already names the group. */
  bare?: boolean;
}) {
  return (
    <section data-testid={testId} aria-label={title} className={cn("flex flex-col gap-3", className)}>
      <div className={cn("flex items-end justify-between gap-4 px-1", bare && "hidden")}>
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-foreground-strong">{title}</h2>
          {description && <p className="mt-0.5 text-base text-muted-foreground">{description}</p>}
        </div>
        {aside && <div className="shrink-0">{aside}</div>}
      </div>
      <div className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
        {children}
      </div>
    </section>
  );
}

export function SettingRow({
  label,
  hint,
  control,
  children,
  testId,
}: {
  label: ReactNode;
  hint?: ReactNode;
  control?: ReactNode;
  /** Content that spans the full row width under the label line. */
  children?: ReactNode;
  testId?: string;
}) {
  return (
    <div data-testid={testId} className="px-5 py-4">
      <div className="flex items-center justify-between gap-6">
        <div className="min-w-0">
          <div className="text-base font-medium text-foreground">{label}</div>
          {hint && <div className="mt-0.5 text-sm text-muted-foreground">{hint}</div>}
        </div>
        {control && <div className="flex shrink-0 items-center gap-2">{control}</div>}
      </div>
      {children}
    </div>
  );
}
