import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The "there is nothing here yet" surface, for every section that can be empty.
 *
 * Replaces the dimmed centred word each view invented for itself — a single
 * `text-muted-foreground` line floating in a black rectangle, which is
 * indistinguishable from a section that failed to load. An empty state is a
 * designed surface with two jobs: say what will appear here, and offer the one
 * action that makes it appear. If a section has no such action, it has no empty
 * state either — it has a sentence.
 *
 * A card, so it is a real object on the page rather than a hole in it, and
 * capped at the form measure so it stays sized to its content: a full-bleed
 * surface never rises above the room it sits in.
 *
 * Locale-free: the caller passes already-translated strings.
 */
export function EmptyState({
  icon,
  title,
  body,
  action,
  className,
}: {
  /** A 20px lucide glyph. Rendered in secondary ink; pass no colour class. */
  icon?: ReactNode;
  /** Optional short headline — skip it when `body` already says everything. */
  title?: string;
  /** One sentence: what appears here, and when. Already translated. */
  body: string;
  /** The one control that fills this section. Omit only if none exists. */
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      data-testid="empty-state"
      className={cn(
        "mx-auto flex w-full max-w-form flex-col items-center gap-3",
        "rounded-lg bg-card px-block py-10 text-center shadow-rim",
        className,
      )}
    >
      {icon && (
        <span
          aria-hidden
          className="text-muted-foreground [&>svg]:h-5 [&>svg]:w-5"
        >
          {icon}
        </span>
      )}
      {title && (
        <p className="text-title font-semibold text-foreground-strong">
          {title}
        </p>
      )}
      <p className="max-w-reading text-body text-muted-foreground">{body}</p>
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}
