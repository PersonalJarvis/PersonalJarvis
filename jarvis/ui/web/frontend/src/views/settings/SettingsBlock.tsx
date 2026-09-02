import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

/**
 * The one shared shell every "Advanced" block (Jarvis-API key, Team mode,
 * Telephony, Wiki) is built from, so the zone reads as a single consistent list
 * of optional integrations instead of four hand-rolled sections.
 *
 * A block is an OBJECT: a card that fills the section it sits in. A 640px
 * form cap was tried and left a column of cards in a sea of black; the
 * section owns the width, the card follows. Separation is fill first:
 * --card carries it, the hairline only finishes the edge, and there is no
 * shadow because a block does not float.
 *
 * The icon used to sit inside a 36px tinted disc. The disc was a wrapper around
 * one glyph and nothing else, and it made the icon the brightest mark in a row
 * whose heading is the actual subject — so it is gone. What remains is a fixed
 * sizing box in secondary ink, the same construction the shared SectionHeader
 * uses, so a glyph can never out-shout its own title.
 */
export function SettingsBlock({
  icon: Icon,
  title,
  description,
  headerRight,
  children,
}: {
  icon: LucideIcon;
  title: ReactNode;
  description?: ReactNode;
  /** Optional top-right control — an enable switch, a status badge, etc. */
  headerRight?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section className="w-full overflow-hidden rounded-lg border border-border bg-card p-block">
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center text-muted-foreground"
        >
          <Icon className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-title font-semibold text-foreground-strong">
            {title}
          </h3>
          {description && (
            <p className="mt-1 text-meta text-muted-foreground">{description}</p>
          )}
        </div>
        {headerRight && (
          <div className="flex shrink-0 items-center pl-2">{headerRight}</div>
        )}
      </div>
      {children && <div className="mt-block">{children}</div>}
    </section>
  );
}

/**
 * A labelled form field.
 *
 * The label was a 10px uppercase micro-label — below the 11px floor and the
 * exact construction that makes an interface read as an admin panel. It is
 * sentence-case `meta` ink now: a field's label is a quiet caption, not a
 * heading, and it reads as one at 13px without shouting.
 */
export function SettingsField({
  label,
  children,
}: {
  label: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-meta text-muted-foreground">
        {label}
      </span>
      {children}
    </label>
  );
}

/**
 * The shared text-input styling used across the settings blocks.
 *
 * A field is a LIFT surface (`--input`), not the room. It used to be painted
 * `bg-background` with a `--border` hairline: inside a card that is a child
 * darker than its parent, and on near-black the outline had nothing behind it,
 * so a text field read as an empty wireframe rather than as somewhere to type.
 * Fill carries it now and the border is gone. Focus is the shared 2px
 * --border-strong ring, never a coloured hairline.
 */
export const settingsInputCls =
  "w-full rounded-md bg-input px-3 py-2 text-body text-foreground placeholder:text-faint-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong";
