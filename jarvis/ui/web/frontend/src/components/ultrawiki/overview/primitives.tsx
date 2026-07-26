/**
 * The three shared pieces of the overview's visual language.
 *
 * They exist as one module because the overview's whole argument is that the
 * numbers on it agree with each other — and numbers agree more convincingly
 * when they are also SET the same way. Before this, every figure in the
 * section was 11 px muted grey Inter, which is how a corpus size, a backlog
 * and a chip label all ended up looking equally unimportant.
 */
import { cn } from "@/lib/utils";

/**
 * A section label phrased as the question the section answers.
 *
 * The checklist module's own doctrine is "answer the question in the order a
 * person asks it"; the overview simply prints those questions, so the reading
 * order is the reasoning order rather than a list of nouns.
 */
export function Eyebrow({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <h3
      className={cn(
        "mb-2 text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground",
        className,
      )}
    >
      {children}
    </h3>
  );
}

/**
 * Every quantity on the screen, in the app's mono face with fixed-width
 * digits and thin-space grouping.
 *
 * Two reasons beyond taste. The panel polls every few seconds, and
 * proportional digits make each refresh nudge the layout — which reads as
 * instability on a screen whose entire job is to look trustworthy. And "4712"
 * is a number you decode; "4 712" is one you see.
 */
export function Num({
  value,
  className,
}: {
  value: number;
  className?: string;
}): JSX.Element {
  return (
    <span className={cn("font-mono tabular-nums", className)}>
      {formatCount(value)}
    </span>
  );
}

/**
 * 4712 → "4 712".
 *
 * A plain space, deliberately: the backend formats its checklist numbers the
 * same way (`health._num`), and a typographically nicer thin space here would
 * make the same figure look subtly different depending on which surface
 * printed it — on a screen whose whole point is that its numbers agree.
 */
export function formatCount(value: number): string {
  return Math.max(0, Math.round(value || 0))
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}
