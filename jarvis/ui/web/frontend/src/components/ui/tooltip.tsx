/**
 * A fast, app-styled replacement for the native `title` attribute.
 *
 * The native tooltip is the wrong tool for a truncated label: it waits a
 * second and a half before appearing, and it is drawn by the OS in whatever
 * grey box the platform ships — a foreign artifact on top of the app's own
 * surfaces. This one appears quickly, wears the same card surface as every
 * other floating panel here, and is rendered in a portal so a clipping
 * ancestor (a scrolling rail, an `overflow: hidden` pane) cannot cut it off.
 *
 * Deliberately NOT interactive, unlike the recap card: a tooltip repeats text
 * that is already on screen but truncated. Anything that needs hovering,
 * clicking or copying belongs in a real popover, not here.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

/** Quick enough to feel immediate, long enough that sweeping the cursor
 * across a list does not flash a tooltip per row. */
const SHOW_DELAY_MS = 180;
const MAX_WIDTH = 320;
/** Gap between the anchor and the bubble. */
const GAP = 8;
const VIEWPORT_MARGIN = 8;

type Side = "right" | "top" | "bottom";

interface Position {
  left: number;
  top: number;
}

function place(anchor: DOMRect, bubble: DOMRect, side: Side): Position {
  let left: number;
  let top: number;
  if (side === "right") {
    left = anchor.right + GAP;
    top = anchor.top + anchor.height / 2 - bubble.height / 2;
    // A rail sits at the screen edge; if the bubble cannot fit to the right,
    // fall through to "below" rather than covering the anchor.
    if (left + bubble.width > window.innerWidth - VIEWPORT_MARGIN) {
      left = anchor.left;
      top = anchor.bottom + GAP;
    }
  } else if (side === "top") {
    left = anchor.left + anchor.width / 2 - bubble.width / 2;
    top = anchor.top - GAP - bubble.height;
  } else {
    left = anchor.left + anchor.width / 2 - bubble.width / 2;
    top = anchor.bottom + GAP;
  }
  left = Math.min(Math.max(left, VIEWPORT_MARGIN), window.innerWidth - bubble.width - VIEWPORT_MARGIN);
  top = Math.min(Math.max(top, VIEWPORT_MARGIN), window.innerHeight - bubble.height - VIEWPORT_MARGIN);
  return { left, top };
}

/**
 * Wraps its children in a span that shows `content` in an app-styled bubble on
 * hover or keyboard focus. Replaces `title=` — never add both, or the native
 * box will race this one.
 */
export function QuickTooltip({
  content,
  side = "right",
  disabled = false,
  className,
  children,
}: {
  /** What the bubble says. An empty string never opens. */
  content: string;
  side?: Side;
  /** Suppress entirely — for rows whose text is not truncated, say. */
  disabled?: boolean;
  /** Classes for the anchor span, so this can slot into an existing layout. */
  className?: string;
  children: ReactNode;
}) {
  const anchorRef = useRef<HTMLSpanElement | null>(null);
  const bubbleRef = useRef<HTMLDivElement | null>(null);
  const timerRef = useRef<number | null>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<Position | null>(null);

  const cancel = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setOpen(false);
    setPosition(null);
  }, []);

  const schedule = useCallback(() => {
    if (disabled || !content.trim()) return;
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      setOpen(true);
    }, SHOW_DELAY_MS);
  }, [disabled, content]);

  useEffect(() => cancel, [cancel]);

  // Two-pass placement: the bubble has to exist to be measured, so it mounts
  // invisible, is measured, and only then receives its coordinates.
  useEffect(() => {
    if (!open) return;
    const anchor = anchorRef.current?.getBoundingClientRect();
    const bubble = bubbleRef.current?.getBoundingClientRect();
    if (!anchor || !bubble) return;
    setPosition(place(anchor, bubble, side));
  }, [open, side]);

  return (
    <span
      ref={anchorRef}
      className={className}
      onMouseEnter={schedule}
      onMouseLeave={cancel}
      onFocus={schedule}
      onBlur={cancel}
      // Selecting the row is the moment the tooltip stops being news.
      onClick={cancel}
    >
      {children}
      {open &&
        createPortal(
          <div
            ref={bubbleRef}
            role="tooltip"
            style={{
              position: "fixed",
              left: position?.left ?? 0,
              top: position?.top ?? 0,
              maxWidth: MAX_WIDTH,
              visibility: position ? "visible" : "hidden",
            }}
            className={cn(
              "pointer-events-none z-[70] rounded-lg border border-border/90 bg-card px-2.5 py-1.5",
              "text-[11.5px] leading-snug text-foreground shadow-[0_10px_28px_-14px_rgba(0,0,0,0.85)]",
              position && "animate-in fade-in-0 zoom-in-95 duration-100",
            )}
          >
            {content}
          </div>,
          document.body,
        )}
    </span>
  );
}
