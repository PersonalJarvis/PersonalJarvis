/**
 * The two pieces the assistant's profile page is set in.
 *
 * `Section` is a chapter of the page: a hairline, a heading, and at most one
 * quiet action on the heading's line. No card around it — the page reads as
 * one document, not as a stack of boxes.
 *
 * `TextAction` is the only kind of secondary control on the page: words in
 * muted ink that turn to full ink on hover. No outline, no icon.
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/utils";

export function Section({
  title,
  action,
  children,
  testId,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <section data-testid={testId} aria-label={title} className="border-t border-border pt-7">
      <div className="mb-4 flex items-baseline justify-between gap-6">
        <h2 className="text-lg font-semibold text-foreground-strong">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

export function TextAction({ className, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...props}
      className={cn(
        "rounded-sm text-sm font-medium text-muted-foreground transition-colors",
        "hover:text-foreground focus-visible:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        "disabled:pointer-events-none disabled:opacity-40",
        className,
      )}
    />
  );
}

/** A label column beside its content, the grammar of the whole page. */
export function SpecRow({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-1 py-3.5 sm:grid-cols-[8rem_minmax(0,1fr)] sm:gap-8">
      <div className="text-base leading-7 text-muted-foreground">{label}</div>
      <div className="min-w-0 max-w-reading text-base leading-7 text-foreground">{children}</div>
    </div>
  );
}
