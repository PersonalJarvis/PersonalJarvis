import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The ONE header every view starts with — that is what gives the app one
 * voice.
 *
 * Title at the xl step (20/600), an optional one-line description in muted
 * ink beneath it, a right-aligned actions slot, and an optional `tabs` slot
 * (a `SectionTabBar`) under the title row. The rhythm is fixed here: 24 px
 * above, 24 px below, so no view decides its own.
 *
 * Locale-free: the caller passes already-translated strings.
 */
export function PageHeader({
  icon,
  title,
  description,
  actions,
  tabs,
  className,
}: {
  /** A 20 px lucide glyph, rendered in muted ink; pass no colour class. */
  icon?: ReactNode;
  title: string;
  description?: string;
  /** Right-aligned controls — the view's own actions, if it has any. */
  actions?: ReactNode;
  /** A `SectionTabBar` (or equivalent) drawn under the title row. */
  tabs?: ReactNode;
  className?: string;
}) {
  return (
    <header
      data-testid="section-header"
      className={cn("flex shrink-0 flex-col pt-6", tabs ? "pb-0" : "pb-6", className)}
    >
      <div className="flex items-start gap-3">
        {icon && (
          <span
            aria-hidden
            className="mt-1 flex h-5 w-5 shrink-0 items-center justify-center text-muted-foreground [&>svg]:h-5 [&>svg]:w-5"
          >
            {icon}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-semibold text-foreground-strong">{title}</h1>
          {description && (
            <p className="mt-1 text-base text-muted-foreground">{description}</p>
          )}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      {tabs && <div className="mt-4">{tabs}</div>}
    </header>
  );
}
