import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

/**
 * The one shared shell every "Advanced" block (Jarvis-API key, Team mode,
 * Telephony, Wiki) is built from, so the zone reads as a single consistent list
 * of optional integrations instead of four hand-rolled sections. A tinted icon
 * tile + title + description on the left, an optional control (a switch, a
 * status badge) on the right, and the block's own body below.
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
    <section className="rounded-2xl border border-border bg-card/60 p-5 backdrop-blur">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border bg-muted text-primary">
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-medium">{title}</h3>
          {description && (
            <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
              {description}
            </p>
          )}
        </div>
        {headerRight && (
          <div className="flex shrink-0 items-center pl-2">{headerRight}</div>
        )}
      </div>
      {children && <div className="mt-4">{children}</div>}
    </section>
  );
}

/** A labelled form field with the shared uppercase micro-label. */
export function SettingsField({
  label,
  children,
}: {
  label: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      {children}
    </label>
  );
}

/** The shared text-input styling used across the settings blocks. */
export const settingsInputCls =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/70 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/40";
