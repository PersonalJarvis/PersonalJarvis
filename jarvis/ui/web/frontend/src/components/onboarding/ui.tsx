/**
 * The handful of pieces every beat of the first-run card is built from.
 *
 * One rule runs through all of them: a beat has exactly ONE way forward that
 * looks like a button — the full-width ink action at its foot. Everything else
 * (later, back, decline, the terms) is quiet text, so the eye never has to
 * choose between two equal buttons.
 */
import { motion, useReducedMotion } from "framer-motion";
import { Check, Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { FOCUS_RING } from "@/components/agentic/controls";
import { cn } from "@/lib/utils";

/** The guide's one easing curve: quick out, soft landing. */
export const EASE_OUT = [0.22, 1, 0.36, 1] as const;

/**
 * Children rise into place one after another — 10 px up and in, 40 ms apart.
 * Nothing grows out of nothing; with reduced motion everything is simply there.
 */
export function Rise({
  index = 0,
  className,
  children,
}: {
  index?: number;
  className?: string;
  children: ReactNode;
}) {
  const reduced = useReducedMotion() ?? false;
  if (reduced) return <div className={className}>{children}</div>;
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.32, ease: EASE_OUT, delay: 0.06 + index * 0.04 }}
    >
      {children}
    </motion.div>
  );
}

/** The beat's one way forward: full width, ink on the room colour. */
export function PrimaryAction({
  children,
  onClick,
  disabled,
  busy,
  testId,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
  testId?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      data-testid={testId ?? "onboarding-primary"}
      className={cn(
        "flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-foreground px-4",
        "text-base font-medium text-background transition-[transform,opacity] duration-150",
        "hover:opacity-90 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card",
      )}
    >
      {busy && <Loader2 aria-hidden className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  );
}

/** Every other action: small, muted text that brightens on hover. */
export function QuietAction({
  children,
  onClick,
  disabled,
  testId,
  className,
}: {
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  testId?: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      className={cn(
        "rounded-sm text-sm text-muted-foreground transition-colors hover:text-foreground",
        "disabled:cursor-not-allowed disabled:opacity-40",
        FOCUS_RING,
        className,
      )}
    >
      {children}
    </button>
  );
}

export type StatusTone = "ok" | "warning" | "error" | "muted";

/**
 * A live result, said in one line: a dot in the status hue and the sentence.
 * Every beat that checks something (a key, the microphone, a permission)
 * answers with one of these, in place, instead of a toast somewhere else.
 */
export function Status({
  tone,
  children,
  testId,
}: {
  tone: StatusTone;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <p
      role="status"
      data-testid={testId}
      data-tone={tone}
      className={cn(
        "flex items-start gap-2 text-sm leading-relaxed",
        tone === "error" ? "text-destructive" : tone === "muted" ? "text-muted-foreground" : "text-foreground",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full",
          tone === "ok" && "bg-success",
          tone === "warning" && "bg-warning",
          tone === "error" && "bg-destructive",
          tone === "muted" && "bg-muted-foreground/60",
        )}
      />
      <span className="min-w-0">{children}</span>
    </p>
  );
}

/** A checkbox sentence — the consent and the wake-word responsibility. */
export function CheckLine({
  checked,
  onChange,
  children,
  testId,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 text-sm leading-relaxed text-foreground">
      <input
        type="checkbox"
        data-testid={testId}
        className={cn("mt-0.5 h-4 w-4 shrink-0 accent-[hsl(var(--accent))]", FOCUS_RING)}
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{children}</span>
    </label>
  );
}

/**
 * One selectable tile of a choice (radio semantics). The selected tile takes
 * the accent edge and a small check; the others stay on the card ground.
 */
export function Option({
  selected,
  onSelect,
  title,
  body,
  badge,
  testId,
}: {
  selected: boolean;
  onSelect: () => void;
  title: string;
  body?: ReactNode;
  badge?: string | null;
  testId?: string;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      data-testid={testId}
      className={cn(
        "relative flex w-full flex-col items-start gap-1 rounded-xl border px-4 py-3 text-left transition-colors",
        selected
          ? "border-accent bg-accent-soft"
          : "border-border bg-background hover:border-border-strong hover:bg-secondary",
        FOCUS_RING,
      )}
    >
      <span className="flex w-full items-center gap-2 pr-6">
        <span className="text-sm font-medium text-foreground">{title}</span>
        {badge && (
          <span className="rounded-full bg-accent-soft px-2 py-0.5 text-micro font-medium text-accent">
            {badge}
          </span>
        )}
      </span>
      {body && <span className="text-sm leading-snug text-muted-foreground">{body}</span>}
      {selected && (
        <span
          aria-hidden
          className="absolute right-3 top-3 flex h-4 w-4 items-center justify-center rounded-full bg-accent text-accent-foreground"
        >
          <Check className="h-3 w-3" strokeWidth={3} />
        </span>
      )}
    </button>
  );
}

/** A key combo from the backend ("f3+f4") drawn as keycaps. */
export function Keycaps({ combo }: { combo: string }) {
  const keys = combo
    .split("+")
    .map((k) => k.trim())
    .filter(Boolean);
  return (
    <span className="inline-flex items-center gap-1 align-middle">
      {keys.map((k, i) => (
        <kbd
          key={`${k}-${i}`}
          className="inline-flex h-6 min-w-6 items-center justify-center rounded-md border border-border-strong bg-background px-1.5 font-mono text-xs uppercase text-foreground"
        >
          {k.replace(/_/g, " ")}
        </kbd>
      ))}
    </span>
  );
}
