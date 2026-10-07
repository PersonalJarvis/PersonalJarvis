/**
 * The look of an icon switch that sits in the middle of the window caption —
 * the Agentic IDE's grid / threads / Verse switch and the Agents page's
 * Verse / agents switch share it, so both read as the same control.
 *
 * A rounded track, one thumb that slides under the segment that is on, and
 * icon-only segments of a fixed width (the thumb moves by exactly that much).
 */

/** Width of one segment; the sliding thumb moves by exactly this much. */
export const CAPTION_SEGMENT_PX = 40;

export const CAPTION_TRACK =
  "relative flex h-7 items-center rounded-full border border-border bg-secondary/70 p-[3px] shadow-rim dark:bg-card";

export const CAPTION_THUMB =
  "absolute inset-y-[3px] left-[3px] rounded-full border border-border-strong/60 bg-background " +
  "shadow-[0_1px_2px_rgb(var(--scrim-rgb)/0.25)] dark:border-border-strong dark:bg-surface-raised " +
  "transition-transform duration-200 ease-out motion-reduce:transition-none";

export const CAPTION_SEGMENT =
  "relative z-[1] flex h-full items-center justify-center rounded-full transition-colors duration-150 " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export const CAPTION_SEGMENT_ON = "text-foreground-strong";
export const CAPTION_SEGMENT_OFF = "text-muted-foreground hover:text-foreground";

/** A lone round icon button beside a caption switch, on the same track. */
export const CAPTION_ICON_BUTTON =
  "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-border bg-secondary/70 " +
  "text-muted-foreground shadow-rim transition-colors duration-150 hover:text-foreground dark:bg-card " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export const CAPTION_ICON_CLASS = "h-4 w-4";
export const CAPTION_ICON_STROKE = 1.9;
