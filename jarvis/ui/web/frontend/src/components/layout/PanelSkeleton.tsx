import { cn } from "@/lib/utils";

/**
 * One skeleton bar. The shape of a thing that has not arrived yet.
 *
 * `sheen` is the theme's "lift this off the surface" channel, so the bar is a
 * pale wash on near-black and an ink wash on paper without either value being
 * written down. Give it the width and height of the real element it stands in
 * for — a skeleton whose bars are a different size from the content that
 * replaces them makes the section jump when the data lands.
 */
export function SkeletonBar({
  className,
  style,
}: {
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <span
      aria-hidden
      className={cn("block rounded-md bg-sheen/[0.06]", className)}
      style={style}
    />
  );
}

/**
 * The loading state for a list, table or card stack.
 *
 * Replaces the two things every section did instead: a centred spinner in an
 * otherwise black rectangle, and — worse — a finished-looking panel full of
 * zeros. A section showing "0 runs" while the request is still in flight is
 * not loading, it is lying: the user reads a working section with no data and
 * goes looking for the bug.
 *
 * So this renders the REAL container at its real height, with a bar where each
 * row will be. Pass the row height the section actually uses; the default is
 * the app's standard list row. It announces itself as busy, so a screen reader
 * hears "loading" rather than walking a wall of empty spans.
 */
export function PanelSkeleton({
  rows = 4,
  rowHeight = 44,
  label,
  className,
}: {
  /** How many rows the real container shows before it scrolls. */
  rows?: number;
  /** The real row height in px, so nothing shifts when the data lands. */
  rowHeight?: number;
  /** Already-translated "loading" text for assistive technology. */
  label?: string;
  className?: string;
}) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label={label}
      data-testid="panel-skeleton"
      className={cn("flex w-full flex-col gap-stack", className)}
    >
      {Array.from({ length: rows }, (_, i) => (
        <SkeletonBar
          key={i}
          style={{ height: rowHeight }}
          // The bars shorten down the stack, the way real rows have unequal
          // titles. A column of identical full-width blocks reads as a
          // rendering fault rather than as content on its way.
          className={i % 3 === 2 ? "w-4/5" : i % 3 === 1 ? "w-11/12" : "w-full"}
        />
      ))}
    </div>
  );
}
