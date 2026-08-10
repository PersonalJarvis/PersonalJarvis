/**
 * The controls the Agentic IDE's own screens are built from.
 *
 * ## Why this section does not use `.btn-primary` / `.btn-ghost`
 *
 * Those two are the app's marketing-weight controls: a full pill, 24 px of
 * horizontal padding, 12 px of vertical padding, and a 32 px yellow glow on
 * hover. They are right on a settings page with four buttons on it and wrong
 * here, where a single screen carries a folder browser, a count, and one row per
 * terminal. Sized like that, the launcher became a column of large soft shapes
 * with no hierarchy left to spend — every control shouted, so none of them led.
 *
 * These are the same design language one size down and one volume quieter:
 * 32 px tall, 6 px radius, no glow, and colour reserved. That reservation is the
 * actual rule and the reason this file exists:
 *
 * * **Yellow means one of two things** — "this is the choice you made" or "this
 *   is the one action that starts something". Nothing else may be yellow. An
 *   accent spent on decoration cannot also mark a selection, and this screen has
 *   real selections to mark.
 * * **Icons carry meaning or they are not there.** A rocket beside "Open" and a
 *   sparkle beside a heading tell the reader nothing they did not already read
 *   in the word next to it.
 *
 * Radii are a closed set across this section: `rounded-control` (6 px) for a
 * control, a row or a tile, and `rounded-surface` (10 px) for a panel. Both are
 * defined in tailwind.config.ts, where the note explains why the theme's own
 * `md`/`lg`/`xl` could not be used: they all resolve to 10-12 px, so nesting
 * them produced no sense of one thing sitting inside another.
 */
import { forwardRef } from "react";
import { cn } from "@/lib/utils";

/**
 * The ONE way a control in this section says it has keyboard focus.
 *
 * Exported rather than kept private because the terminal panes cannot use the
 * controls below and still need this: a pane header is coloured by the
 * TERMINAL's appearance — a light pane inside a dark app is a supported
 * combination — so its buttons carry their own palette rather than the theme's.
 * What they must not carry is their own idea of what "focused" looks like.
 *
 * Until this became shared, the pane header simply had none: every one of its
 * five actions was reachable by Tab and invisible once it got there, which on a
 * wall of a dozen panes means a keyboard user has no way to tell which terminal
 * they are about to close.
 */
export const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60";

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  /**
   * `primary` starts something and there is at most one per screen; `quiet` is
   * every other action; `subtle` is for controls that live inside a row and
   * should recede until the row is hovered.
   */
  variant?: "primary" | "quiet" | "subtle";
};

const BASE =
  "inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-control px-3 " +
  "text-sm font-medium transition-colors " +
  `${FOCUS_RING} ` +
  "disabled:cursor-not-allowed disabled:opacity-40";

const VARIANTS: Record<NonNullable<ButtonProps["variant"]>, string> = {
  primary: "bg-primary text-primary-foreground hover:bg-primary/90",
  quiet:
    "border border-border bg-card/60 text-foreground hover:border-border hover:bg-secondary",
  subtle: "text-muted-foreground hover:bg-secondary hover:text-foreground",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button({ variant = "quiet", className, type, ...rest }, ref) {
    return (
      <button
        ref={ref}
        // Every button in this section sits inside the launcher, which is not a
        // form — but the folder path field IS wrapped in one, and a button
        // without an explicit type submits it. Defaulting here rather than at
        // each call site is what stops one forgotten attribute from reloading
        // the view.
        type={type ?? "button"}
        className={cn(BASE, VARIANTS[variant], className)}
        {...rest}
      />
    );
  },
);

/**
 * A square button that is only an icon.
 *
 * Separate from `Button` because the padding rule is different — an icon needs
 * equal space on all four sides — and because it MUST be given a label. An
 * unlabelled icon button is invisible to a screen reader and unguessable to
 * everyone else, so the prop is required rather than optional.
 */
type IconButtonProps = Omit<
  React.ButtonHTMLAttributes<HTMLButtonElement>,
  "aria-label"
> & {
  label: string;
  /** Matches `Button`'s scale, so the two line up in a row. */
  size?: "sm" | "md";
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  function IconButton({ label, size = "md", className, type, ...rest }, ref) {
    return (
      <button
        ref={ref}
        type={type ?? "button"}
        aria-label={label}
        title={rest.title ?? label}
        className={cn(
          "inline-flex shrink-0 items-center justify-center rounded-control " +
            "text-muted-foreground transition-colors " +
            "hover:bg-secondary hover:text-foreground " +
            `${FOCUS_RING} ` +
            "disabled:cursor-not-allowed disabled:opacity-40",
          size === "sm" ? "h-6 w-6" : "h-8 w-8",
          className,
        )}
        {...rest}
      />
    );
  },
);

/**
 * A text field at the same scale as `Button`.
 *
 * The focus treatment is a border change rather than a ring on purpose: several
 * of these sit in one row beside chips and selects, and a 2 px ring around each
 * would make a focused row look like an error state.
 */
export const Field = forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(function Field({ className, ...rest }, ref) {
  return (
    <input
      ref={ref}
      className={cn(
        "h-8 min-w-0 rounded-control border border-border bg-background px-2.5 text-sm " +
          "text-foreground placeholder:text-muted-foreground/70 " +
          "outline-none transition-colors focus:border-primary/60",
        className,
      )}
      {...rest}
    />
  );
});

/**
 * A panel — the one container shape this section uses.
 *
 * `title` is rendered as a small caps-tracked label rather than a heading with
 * its own font size. The launcher has one real heading; everything else is a
 * label on a region, and giving those heading-sized type is what made the old
 * screen read as four unrelated cards stacked in a column.
 */
export function Panel({
  title,
  aside,
  className,
  bodyClassName,
  children,
  ...rest
}: React.HTMLAttributes<HTMLElement> & {
  title?: React.ReactNode;
  /** Controls that belong to this panel, pinned to the right of its label. */
  aside?: React.ReactNode;
  bodyClassName?: string;
}) {
  return (
    <section
      className={cn(
        "flex min-h-0 min-w-0 flex-col rounded-surface border border-border/70 bg-card/30",
        className,
      )}
      {...rest}
    >
      {(title || aside) && (
        <div className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-border/70 px-3">
          {title ? <SectionLabel>{title}</SectionLabel> : <span />}
          {aside && (
            <div className="flex shrink-0 items-center gap-1.5">{aside}</div>
          )}
        </div>
      )}
      <div className={cn("flex min-h-0 flex-1 flex-col", bodyClassName)}>
        {children}
      </div>
    </section>
  );
}

/** The one label style for a region of this screen. */
export function SectionLabel({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "truncate text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground",
        className,
      )}
    >
      {children}
    </span>
  );
}

/**
 * Something the user should know before they press Open.
 *
 * One line, a rule down its left edge, no filled background and no icon. The
 * previous version was a filled box with a border, an alert glyph and its own
 * colour — three ways of saying "notice" stacked on one another, which at the
 * top of a screen out-shouted the screen. A coloured edge is enough to find and
 * quiet enough to walk past, and walking past it is the common case: both of
 * these describe the machine, not the choice being made.
 */
export function Notice({
  tone,
  children,
}: {
  tone: "warning" | "error";
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-2 gap-y-1 border-l-2 py-1 pl-3 text-sm",
        tone === "error"
          ? "border-destructive/70 text-destructive"
          : "border-amber-400/70 text-amber-200/90",
      )}
    >
      {children}
    </div>
  );
}
