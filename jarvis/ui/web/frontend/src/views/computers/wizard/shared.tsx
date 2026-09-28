/**
 * Small building blocks the add-computer wizard's steps share: the option
 * card, the error line and the wizard's own types.
 */
import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ProviderInfo } from "@/lib/computersApi";

/** How the new computer is reached. ``api`` imports it from the provider. */
export type Method = "api" | "password" | "private_key" | "jarvis_key";

/** The gallery's pick: a catalog provider, or a VM on this computer. */
export type Pick = { kind: "provider"; provider: ProviderInfo } | { kind: "local" };

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p
      role="alert"
      className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-foreground"
    >
      {message}
    </p>
  );
}

/** A selectable row with an icon, a title, one line of why and an optional badge. */
export function OptionCard({
  active,
  onClick,
  icon,
  title,
  body,
  badge,
  testId,
}: {
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
  title: string;
  body: string;
  badge?: string;
  testId?: string;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      data-testid={testId}
      onClick={onClick}
      className={cn(
        "flex w-full items-start gap-4 rounded-lg border px-4 py-3.5 text-left transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active ? "border-accent bg-accent-soft" : "border-border hover:border-border-strong hover:bg-secondary/40",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "flex h-9 w-9 shrink-0 items-center justify-center rounded-md [&>svg]:h-[18px] [&>svg]:w-[18px]",
          active ? "bg-background text-foreground" : "bg-secondary text-muted-foreground",
        )}
      >
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2 text-base font-medium text-foreground-strong">
          {title}
          {badge && (
            <span className="rounded-full border border-border px-2 py-0.5 text-xs font-medium text-foreground-secondary">
              {badge}
            </span>
          )}
        </span>
        <span className="mt-0.5 block text-sm text-muted-foreground">{body}</span>
      </span>
      <span
        aria-hidden
        className={cn(
          "mt-1 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border",
          active ? "border-accent bg-accent text-accent-foreground" : "border-border-strong",
        )}
      >
        {active && <Check className="h-3 w-3" />}
      </span>
    </button>
  );
}

/** A compact pick-one row of chips (sizes, images). */
export function Choice<T extends string | number>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { id: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-1.5">
      {options.map((o) => {
        const active = o.id === value;
        return (
          <button
            key={String(o.id)}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.id)}
            className={cn(
              "h-8 rounded-md border px-3 text-sm font-medium tabular-nums transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              active
                ? "border-accent bg-accent-soft text-foreground-strong"
                : "border-border text-muted-foreground hover:border-border-strong hover:text-foreground",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
