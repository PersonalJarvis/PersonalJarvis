import { Fragment, type ReactNode, useId } from "react";

import { cn } from "@/lib/utils";

/*
 * The building blocks every tab of the voice section is drawn with.
 *
 * One measure, one rhythm, one surface: each tab is a centred reading column
 * (`VoicePage`), cut into sections (`VoiceSection`: a heading, one quiet line,
 * optional actions on the right) whose content sits on one grouped surface
 * (`VoiceGroup`) split by hairlines into rows (`VoiceRow`: name and one
 * sentence on the left, the control on the right). Five tabs built from the
 * same four pieces read as one product instead of five screens that happen to
 * share a tab bar.
 *
 * Locale-free: callers pass already-translated strings.
 */

/** The scrolling page of one tab: a centred column at the section's measure. */
export function VoicePage({
  children,
  testId,
  className,
}: {
  children: ReactNode;
  testId?: string;
  className?: string;
}) {
  return (
    <div className="h-full min-h-0 overflow-y-auto scrollbar-jarvis" data-testid={testId}>
      <div
        className={cn(
          "profile-rise mx-auto flex w-full max-w-3xl flex-col gap-10 px-4 pb-16 pt-8 sm:px-8",
          className,
        )}
      >
        {children}
      </div>
    </div>
  );
}

/** A heading with an optional line under it and actions on the right, then its content. */
export function VoiceSection({
  title,
  description,
  actions,
  children,
  id,
  testId,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  /** Right-aligned beside the heading: a search field, a "New" button. */
  actions?: ReactNode;
  children: ReactNode;
  id?: string;
  testId?: string;
  className?: string;
}) {
  const headingId = useId();
  return (
    <section
      id={id}
      aria-labelledby={headingId}
      data-testid={testId}
      className={cn("flex scroll-mt-6 flex-col gap-3", className)}
    >
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 basis-56">
          <h2 id={headingId} className="text-base font-semibold text-foreground-strong">
            {title}
          </h2>
          {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

/** The grouped surface: a rounded card whose direct children are split by hairlines. */
export function VoiceGroup({
  children,
  divided = true,
  className,
  testId,
}: {
  children: ReactNode;
  divided?: boolean;
  className?: string;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      className={cn(
        "overflow-hidden rounded-xl border border-border bg-card",
        divided && "divide-y divide-border",
        className,
      )}
    >
      {children}
    </div>
  );
}

/**
 * One row: an optional leading mark, a name and one sentence on the left, the
 * control on the right. On a narrow window the control drops under the text.
 * `children` render under the text at the row's own inset.
 */
export function VoiceRow({
  icon,
  title,
  description,
  control,
  children,
  id,
  testId,
  className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  control?: ReactNode;
  children?: ReactNode;
  id?: string;
  testId?: string;
  className?: string;
}) {
  return (
    <div id={id} data-testid={testId} className={cn("scroll-mt-6 px-4 py-3.5 sm:px-5", className)}>
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
        <div className="flex min-w-0 flex-1 basis-60 items-start gap-3">
          {icon && <VoiceIcon>{icon}</VoiceIcon>}
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-foreground">{title}</div>
            {description && (
              <div className="mt-0.5 text-sm text-muted-foreground">{description}</div>
            )}
          </div>
        </div>
        {control && <div className="flex shrink-0 flex-wrap items-center gap-2">{control}</div>}
      </div>
      {children && <div className={cn("mt-3 space-y-2", icon && "sm:pl-11")}>{children}</div>}
    </div>
  );
}

/** A 32 px rounded square holding a row's glyph, in body ink. */
export function VoiceIcon({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-secondary text-foreground [&>svg]:h-4 [&>svg]:w-4",
        className,
      )}
    >
      {children}
    </span>
  );
}

/** A quiet lifted block inside a row: a hint, a result, a warning. */
export function VoiceNote({
  children,
  tone = "default",
  icon,
  className,
  testId,
}: {
  children: ReactNode;
  tone?: "default" | "success" | "warning" | "error";
  icon?: ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      role={tone === "error" || tone === "warning" ? "status" : undefined}
      className={cn(
        "flex items-start gap-2.5 rounded-lg bg-secondary px-3 py-2.5 text-sm text-foreground",
        className,
      )}
    >
      {icon && (
        <span
          aria-hidden="true"
          className={cn(
            "mt-0.5 shrink-0 [&>svg]:h-4 [&>svg]:w-4",
            tone === "default" && "text-muted-foreground",
            tone === "success" && "text-success",
            tone === "warning" && "text-warning",
            tone === "error" && "text-destructive",
          )}
        >
          {icon}
        </span>
      )}
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/** Keycaps for labels that are already display text ("Ctrl", "Alt", "Space"). */
export function KeyCombo({
  keys,
  size = "sm",
  className,
}: {
  keys: readonly string[];
  /** `lg` is the hero size on the Dictation tab; `sm` sits inside rows. */
  size?: "sm" | "lg";
  className?: string;
}) {
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-1", size === "lg" && "gap-1.5", className)}>
      {keys.map((key, i) => (
        <Fragment key={`${key}-${i}`}>
          {i > 0 && (
            <span aria-hidden="true" className="text-xs text-muted-foreground">
              +
            </span>
          )}
          <kbd
            className={cn(
              "inline-flex items-center justify-center rounded-md border border-border-strong bg-secondary font-sans font-medium text-foreground-strong shadow-[inset_0_-1px_0_hsl(var(--border-strong))]",
              size === "sm" ? "h-6 min-w-6 px-1.5 text-xs" : "h-9 min-w-9 px-2.5 text-base",
            )}
          >
            {key}
          </kbd>
        </Fragment>
      ))}
    </span>
  );
}

/** A small round state mark; `live` pulses (and stands still for reduced motion). */
export function StatusDot({
  tone,
  live = false,
  className,
}: {
  tone: "ready" | "live" | "warning" | "error" | "idle";
  live?: boolean;
  className?: string;
}) {
  return (
    <span aria-hidden="true" className={cn("relative inline-flex h-2 w-2 shrink-0", className)}>
      {live && (
        <span
          className={cn(
            "absolute inset-0 animate-ping rounded-full opacity-60 motion-reduce:hidden",
            tone === "live" ? "bg-accent" : "bg-success",
          )}
        />
      )}
      <span
        className={cn(
          "relative inline-flex h-2 w-2 rounded-full",
          tone === "ready" && "bg-success",
          tone === "live" && "bg-accent",
          tone === "warning" && "bg-warning",
          tone === "error" && "bg-destructive",
          tone === "idle" && "bg-muted-foreground/50",
        )}
      />
    </span>
  );
}

/** A compact pill that states something ("Ready", "Recommended", "3 words"). */
export function VoiceTag({
  children,
  tone = "default",
  className,
}: {
  children: ReactNode;
  tone?: "default" | "accent" | "success" | "warning" | "error";
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex h-5 shrink-0 items-center gap-1.5 rounded-md px-1.5 text-xs font-medium",
        tone === "default" && "bg-secondary text-muted-foreground",
        tone === "accent" && "bg-accent-soft text-accent",
        tone === "success" && "bg-success/10 text-success",
        tone === "warning" && "bg-warning/10 text-warning",
        tone === "error" && "bg-destructive/10 text-destructive",
        className,
      )}
    >
      {children}
    </span>
  );
}
