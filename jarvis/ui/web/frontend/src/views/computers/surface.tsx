/**
 * Portions adapted from pingdotgg/t3code @ 12069ee (apps/web settingsLayout,
 * SettingsGroup, EnvironmentRow, ConnectionStatusDot, ui/empty), MIT License,
 * Copyright (c) 2026 T3 Tools Inc. Full text: third_party/t3code/LICENSE.
 *
 * The surface of the Computers page: a muted section heading with an action
 * on its right, one grouped card whose rows are split by hairlines, a setting
 * row (title, one short line, control), a machine row (glyph, name, one
 * subtitle line, controls), the status dot and the empty state with its
 * fanned icon tiles. Theme tokens only, so light and dark need no second path.
 */
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import type { Tone } from "./parts";

/** A muted heading over one grouped card. Explanatory copy belongs to the rows. */
export function Section({
  title,
  action,
  children,
  className,
  testId,
}: {
  title: string;
  /** Right of the heading: a small text action such as "Add computer". */
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <section className={cn("space-y-2.5", className)} data-testid={testId}>
      <div className="flex min-h-7 items-center justify-between gap-4 px-3 sm:px-4">
        <h2 className="text-base font-normal text-foreground-secondary">{title}</h2>
        {action && <div className="flex min-h-7 items-center justify-end gap-1">{action}</div>}
      </div>
      <Group>{children}</Group>
    </section>
  );
}

/** The grouped card: rounded, a soft rim, hairlines between its rows. */
export function Group({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border border-border bg-card/60 text-foreground",
        "[&>*+*]:border-t [&>*+*]:border-border",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** One setting: title and a short line on the left, its control on the right. */
export function Row({
  title,
  description,
  status,
  control,
  children,
  testId,
}: {
  title: ReactNode;
  description?: ReactNode;
  /** A quiet extra line under the description: a note, a fingerprint. */
  status?: ReactNode;
  control?: ReactNode;
  children?: ReactNode;
  testId?: string;
}) {
  return (
    <div data-testid={testId} className={cn("px-3 sm:px-4", children ? "pb-2 pt-3" : "py-3")}>
      <div className="flex flex-col gap-3 sm:grid sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center sm:gap-8">
        <div className="min-w-0 space-y-1">
          <h3 className="flex min-h-5 items-center gap-1.5 text-base font-medium text-foreground-strong">{title}</h3>
          {description && <p className="max-w-xl text-sm leading-normal text-muted-foreground">{description}</p>}
          {status && <div className="pt-0.5 text-xs text-foreground-faint">{status}</div>}
        </div>
        {control && <div className="flex min-w-0 shrink-0 items-center gap-2 sm:justify-end">{control}</div>}
      </div>
      {children}
    </div>
  );
}

const DOT: Record<Tone, string> = {
  ok: "bg-success",
  busy: "bg-warning",
  warn: "bg-warning",
  error: "bg-destructive",
  off: "bg-foreground-faint",
};

/** The connection dot: a halo pings only while the state is in motion. */
export function StatusDot({ tone, label }: { tone: Tone; label?: string }) {
  return (
    <span
      className="relative flex h-3 w-3 shrink-0 items-center justify-center"
      role={label ? "img" : undefined}
      aria-label={label}
    >
      {tone === "busy" && (
        <span className="absolute inline-flex h-full w-full rounded-full bg-warning/60 motion-safe:animate-ping" />
      )}
      <span className={cn("relative inline-flex h-2 w-2 rounded-full", DOT[tone])} />
    </span>
  );
}

const MEDIA_TILE =
  "flex h-9 w-9 items-center justify-center rounded-md border border-border bg-card text-foreground shadow-sm [&_svg]:h-[18px] [&_svg]:w-[18px]";

/** A centred empty state: three fanned icon tiles, a title, one or two lines. */
export function Empty({
  icon,
  title,
  description,
  children,
  testId,
}: {
  icon: ReactNode;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      className="flex min-h-[13rem] min-w-0 flex-col items-center justify-center gap-0 text-balance px-6 py-10 text-center"
    >
      <div className="relative mb-6" aria-hidden>
        <div className={cn(MEDIA_TILE, "absolute bottom-px origin-bottom-left -translate-x-0.5 -rotate-[10deg] scale-[0.84] shadow-none")} />
        <div className={cn(MEDIA_TILE, "absolute bottom-px origin-bottom-right translate-x-0.5 rotate-[10deg] scale-[0.84] shadow-none")} />
        <div className={cn(MEDIA_TILE, "relative")}>{icon}</div>
      </div>
      <div className="flex max-w-sm flex-col items-center">
        <div className="text-lg font-semibold text-foreground-strong">{title}</div>
        {description && <div className="mt-1 text-sm text-muted-foreground">{description}</div>}
      </div>
      {children}
    </div>
  );
}

/** Small ghost text action for a section heading: icon plus a short label. */
export const sectionActionCls =
  "inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 [&>svg]:h-3.5 [&>svg]:w-3.5";
