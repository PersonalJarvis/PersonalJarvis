/**
 * The draggable seam between two panes — the one grip the whole app uses.
 *
 * It exists once because a splitter is mostly a set of small decisions that
 * must not differ from screen to screen: how wide the hit target is, what the
 * cursor turns into, whether a double-click resets, and whether the keyboard
 * can move it at all. Two hand-rolled copies drift within a week.
 *
 * Two details are worth knowing:
 *
 * * **The seam you see is 1px; the seam you can hit is 6px.** A one-pixel
 *   target is a coordination test, not a control — so the visible line is thin
 *   and the transparent grab area around it is six times wider.
 * * **It is reachable without a mouse.** `role="separator"` plus a tab stop and
 *   the arrow keys mean the layout can be adjusted from the keyboard, which is
 *   also the only way to recover a pane that was dragged shut on a touchpad.
 */
import { forwardRef } from "react";
import { cn } from "@/lib/utils";

/** How the seam itself runs — not the axis it moves along. */
export type PaneResizerOrientation = "vertical" | "horizontal";

export interface PaneResizerProps {
  /** ``vertical`` = an up-and-down line between two columns (drag sideways). */
  orientation?: PaneResizerOrientation;
  /** Begin a drag — wire to `useResizablePane`'s ``startResize``. */
  onPointerDown: (e: React.PointerEvent) => void;
  /** Restore the default size — wire to ``reset``. */
  onDoubleClick: () => void;
  /** Keyboard equivalent of a drag, in px — wire to ``nudge``. */
  onNudge?: (delta: number) => void;
  /** Current pane size exposed to assistive technology. */
  valueNow?: number;
  /** Smallest pane size exposed to assistive technology. */
  valueMin?: number;
  /** Largest pane size exposed to assistive technology. */
  valueMax?: number;
  /** ID of the pane whose size this separator controls. */
  controls?: string;
  /** True while a drag is in progress, so the seam can light up. */
  active: boolean;
  /** Hover tooltip. Also used as the accessible name. */
  title: string;
  /**
   * Where to put the seam, for layouts that place it rather than flow it.
   *
   * A splitter between two flex children needs nothing here — it sits in the
   * flow between them. A tiled workspace has no such flow: its panes are
   * positioned by fraction, so its seams are too, and they arrive as absolute
   * coordinates. The look and the behaviour stay in this one component either
   * way, which is the whole reason it exists.
   */
  style?: React.CSSProperties;
  /** Extra classes for the same reason as `style`. */
  className?: string;
  /** Overrides the default test id, for screens that render several seams. */
  testId?: string;
}

/** How far one arrow-key press moves the seam. */
const KEY_STEP_PX = 16;
/** …and one press with Shift held, for crossing the screen quickly. */
const KEY_STEP_COARSE_PX = 64;

/**
 * The ref is forwarded because a seam is not always laid out by React.
 *
 * A tiled workspace repaints its boxes directly during a drag — see
 * `usePaneWeights` — and the seam being dragged has to travel with them. It is
 * the one element in the gesture the user is actually looking at, so leaving it
 * to a re-render would make the line lag behind the pointer holding it.
 */
export const PaneResizer = forwardRef<HTMLDivElement, PaneResizerProps>(function PaneResizer(
  {
    orientation = "vertical",
    onPointerDown,
    onDoubleClick,
    onNudge,
    valueNow,
    valueMin,
    valueMax,
    controls,
    active,
    title,
    style,
    className,
    testId,
  },
  ref,
) {
  const vertical = orientation === "vertical";

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!onNudge) return;
    const step = e.shiftKey ? KEY_STEP_COARSE_PX : KEY_STEP_PX;
    // The keys that make the pane BIGGER are the ones pointing away from it,
    // which is the same relationship the drag has.
    const byKey: Record<string, number> = vertical
      ? { ArrowRight: step, ArrowLeft: -step }
      : { ArrowUp: step, ArrowDown: -step };
    const delta = byKey[e.key];
    if (delta === undefined) return;
    e.preventDefault();
    onNudge(delta);
  };

  return (
    <div
      ref={ref}
      role="separator"
      aria-orientation={orientation}
      aria-label={title}
      aria-valuenow={valueNow}
      aria-valuemin={valueMin}
      aria-valuemax={valueMax}
      aria-controls={controls}
      title={title}
      tabIndex={0}
      data-testid={testId ?? `pane-resizer-${orientation}`}
      onPointerDown={onPointerDown}
      onDoubleClick={onDoubleClick}
      onKeyDown={onKeyDown}
      style={style}
      className={cn(
        "group relative z-10 flex shrink-0 touch-none select-none outline-none",
        vertical
          ? "w-1.5 cursor-col-resize items-stretch"
          : "h-1.5 cursor-row-resize justify-stretch",
        className,
      )}
    >
      {/* The visible line — it replaces the border it sits on top of. */}
      <span
        aria-hidden
        className={cn(
          "pointer-events-none absolute transition-colors",
          vertical
            ? "inset-y-0 left-1/2 w-px -translate-x-1/2"
            : "inset-x-0 top-1/2 h-px -translate-y-1/2",
          active
            ? "bg-primary"
            : "bg-border group-hover:bg-primary/60 group-focus-visible:bg-primary/60",
        )}
      />
      {/* A short thicker stub in the middle: without it the seam reads as a
          plain border and nobody discovers it can be moved. */}
      <span
        aria-hidden
        className={cn(
          "pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full transition-colors",
          vertical ? "h-8 w-[3px]" : "h-[3px] w-8",
          active
            ? "bg-primary"
            : "bg-border group-hover:bg-primary/70 group-focus-visible:bg-primary/70",
        )}
      />
    </div>
  );
});
