import { cn } from "@/lib/utils";

/**
 * The mark that says work is happening right now: an accent core inside two
 * rings that expand and fade away.
 *
 * It is the ONE live mark the product owns. The agent chat used to spin a
 * borrowed CLI asterisk instead (maintainer, 2026-08-25: "dieses komische
 * Denksymbol"), which read as a foreign glyph next to the home chat's core —
 * two answers to the same question on two screens of the same app.
 *
 * Under reduced motion the rings stand still behind the core, which still
 * reads as "in progress" beside the ticking clock (index.css).
 */
export function LiveCore({ className }: { className?: string }) {
  return (
    <span
      className={cn("relative flex h-2.5 w-2.5 shrink-0", className)}
      aria-hidden
      data-testid="live-core"
    >
      <span className="thinking-ring absolute inline-flex h-full w-full rounded-full bg-primary/50" />
      <span className="thinking-ring absolute inline-flex h-full w-full rounded-full bg-primary/50 [animation-delay:0.9s]" />
      <span className="thinking-core relative inline-flex h-2.5 w-2.5 rounded-full bg-foreground/70" />
    </span>
  );
}
