import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The "there is nothing here yet" surface, for every view that can be empty.
 *
 * Centred, capped at 420 px, with the glyph in a 48 px --secondary square so
 * the state reads as designed rather than as a section that failed to load.
 * Two jobs: say what will appear here, and offer the action that makes it
 * appear — at most two, the first of them primary.
 *
 * Locale-free: the caller passes already-translated strings.
 */
export function EmptyState({
  icon,
  title,
  description,
  actions,
  className,
}: {
  /** A lucide glyph; sized here, pass no size or colour class. */
  icon?: ReactNode;
  title: string;
  /** One or two sentences: what appears here, and when. Already translated. */
  description?: string;
  /** Up to two controls — a `default` Button first, an `outline` one second. */
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div
      data-testid="empty-state"
      className={cn(
        "mx-auto flex w-full max-w-[420px] flex-col items-center gap-4 py-12 text-center",
        className,
      )}
    >
      {icon && (
        <span
          aria-hidden
          className="flex h-12 w-12 items-center justify-center rounded-lg bg-secondary text-muted-foreground [&>svg]:h-5 [&>svg]:w-5"
        >
          {icon}
        </span>
      )}
      <div className="flex flex-col gap-1">
        <p className="text-lg font-semibold text-foreground-strong">{title}</p>
        {description && (
          <p className="text-base text-muted-foreground">{description}</p>
        )}
      </div>
      {actions && <div className="flex flex-wrap items-center justify-center gap-2">{actions}</div>}
    </div>
  );
}
