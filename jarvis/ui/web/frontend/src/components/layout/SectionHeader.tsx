import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The ONE header every section of the app wears.
 *
 * Replaces the ~46 hand-rolled header rows the views grew independently — each
 * with its own size, weight, ink and padding, several of them a tiny all-caps
 * label over a 12px title. A header is a role, not a layout each view gets to
 * reinvent, and a product whose 46 screens introduce themselves 46 different
 * ways reads as 46 products.
 *
 * The type is fixed here so a call site cannot pick a size: the title is the
 * `page` step (20/600) in the ink ceiling, the subtitle is `meta` in secondary
 * ink, and the icon is a 20px glyph in the same secondary ink — never brighter
 * than the heading it labels. The rhythm is fixed too: 28px above (the page
 * step) and 32px below (the group step), which is the gap that was missing
 * everywhere and is why sections read as one undifferentiated mesh.
 *
 * Locale-free on purpose: the caller passes already-translated strings, the
 * same way the sidebar's rows do, so this component never grows a dependency
 * on the i18n bundle.
 */
export function SectionHeader({
  icon,
  title,
  subtitle,
  actions,
  className,
}: {
  /** A 20px lucide glyph. Rendered in secondary ink; pass no colour class. */
  icon?: ReactNode;
  /** The section's name, already translated. */
  title: string;
  /** One line saying what the section is for. Longer than a line: cut it. */
  subtitle?: string;
  /** Right-aligned controls — the section's own actions, if it has any. */
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header
      data-testid="section-header"
      className={cn("flex shrink-0 items-start gap-3 pb-group pt-7", className)}
    >
      {icon && (
        // A sizing box rather than a decorative wrapper: it pins every
        // section's glyph to the same 20px square on the title's first line,
        // and to the one ink an icon is allowed to carry.
        <span
          aria-hidden
          className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center text-muted-foreground [&>svg]:h-5 [&>svg]:w-5"
        >
          {icon}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-page font-semibold text-foreground-strong">
          {title}
        </h1>
        {subtitle && (
          <p className="mt-1 text-meta text-muted-foreground">{subtitle}</p>
        )}
      </div>
      {actions && (
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
      )}
    </header>
  );
}
