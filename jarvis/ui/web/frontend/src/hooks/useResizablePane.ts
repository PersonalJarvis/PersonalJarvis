import { useCallback, useEffect, useRef, useState } from "react";

/** Which axis the grip travels along: ``x`` sizes a width, ``y`` a height. */
export type ResizeAxis = "x" | "y";

/**
 * Which EDGE of the resized pane the grip sits on.
 *
 * It decides the sign of the drag, and getting it wrong makes a splitter feel
 * inverted — the pane shrinks while the pointer pulls it open. A grip on the
 * ``end`` edge (right of a sidebar, bottom of a panel) grows the pane when the
 * pointer moves further along the axis; a grip on the ``start`` edge (top of a
 * bottom bar) grows it when the pointer moves BACK along the axis.
 */
export type ResizeHandleEdge = "start" | "end";

export interface ResizablePaneOptions {
  /** localStorage key the settled size is persisted under. */
  storageKey: string;
  /** Size used when nothing is stored yet (px). */
  defaultSize: number;
  /** Lower bound (px) — the pane can never get smaller than this. */
  min: number;
  /** Upper bound (px) — keeps the pane from swallowing its neighbour. */
  max: number;
  /** Width (``x``, the default) or height (``y``). */
  axis?: ResizeAxis;
  /** Edge the grip sits on — see `ResizeHandleEdge`. Defaults to ``end``. */
  handle?: ResizeHandleEdge;
}

export interface ResizablePane {
  /** Current pane size in px along `axis` (already clamped). */
  size: number;
  /** True while the user is actively dragging the grip. */
  isResizing: boolean;
  /** Attach to the grip's ``onPointerDown`` to begin a drag. */
  startResize: (e: React.PointerEvent) => void;
  /** Snap back to ``defaultSize`` (wire to the grip's ``onDoubleClick``). */
  reset: () => void;
  /** Move the seam by ``delta`` px — the keyboard equivalent of a drag. */
  nudge: (delta: number) => void;
  /**
   * Jump straight to ``px``.
   *
   * For the controls that name ONE size rather than nudge the current one — a
   * "show the prompt bar" button, a collapse toggle. Those cannot use `reset`
   * once the default IS the collapsed size, which is exactly the case for a
   * pane that starts shut.
   */
  resize: (px: number) => void;
}

/**
 * Clamp a pixel size into the ``[min, max]`` band and round to a whole pixel.
 *
 * Pulled out as a free function so the boundary maths is unit-testable without
 * a DOM — the drag interaction itself is verified live in the app.
 */
export function clampSize(value: number, min: number, max: number): number {
  if (Number.isNaN(value)) return min;
  // A caller whose max is computed from a measured container can hand us an
  // upper bound below the lower one on a very small window. Honouring `min`
  // there would overflow the container, so the measured ceiling wins.
  if (max < min) return Math.round(max);
  return Math.min(max, Math.max(min, Math.round(value)));
}

/**
 * Drag-to-resize state for one splitter seam, on either axis.
 *
 * The settled size is persisted to ``localStorage`` so the layout survives a
 * reload. Pointer listeners live on ``window`` (not the thin grip) for the
 * whole drag, which is what lets the cursor wander off the 6px handle without
 * dropping the drag — the canonical splitter behaviour.
 */
export function useResizablePane({
  storageKey,
  defaultSize,
  min,
  max,
  axis = "x",
  handle = "end",
}: ResizablePaneOptions): ResizablePane {
  const [size, setSize] = useState<number>(() =>
    clampSize(loadSize(storageKey, defaultSize), min, max),
  );
  const [isResizing, setIsResizing] = useState(false);

  // Drag anchors — refs so the move handler never reads a stale closure.
  const startPoint = useRef(0);
  const startSize = useRef(size);

  const startResize = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      startPoint.current = axis === "x" ? e.clientX : e.clientY;
      // A measured maximum can shrink under a stored preference. Anchor the
      // gesture to the size actually on screen so it responds immediately.
      startSize.current = clampSize(size, min, max);
      setIsResizing(true);
    },
    [axis, size, min, max],
  );

  const reset = useCallback(() => setSize(defaultSize), [defaultSize]);

  const nudge = useCallback(
    (delta: number) =>
      setSize((current) =>
        clampSize(clampSize(current, min, max) + delta, min, max),
      ),
    [min, max],
  );

  const resize = useCallback(
    (px: number) => setSize(clampSize(px, min, max)),
    [min, max],
  );

  // Global pointer listeners are armed only while dragging.
  useEffect(() => {
    if (!isResizing) return;

    /*
     * One size change per FRAME, not one per pointer event.
     *
     * A pointer emits up to 120 moves a second and this size is a layout: in
     * the Agentic IDE it decides the height of every terminal above the prompt
     * bar, so an unthrottled drag re-laid out the whole workspace twice per
     * frame and then threw the first one away. The pointer's latest position is
     * kept and read when the frame comes.
     */
    let frame: number | undefined;
    let point = startPoint.current;

    const apply = () => {
      frame = undefined;
      const travelled = point - startPoint.current;
      const grown = handle === "end" ? travelled : -travelled;
      setSize(clampSize(startSize.current + grown, min, max));
    };

    const onMove = (e: PointerEvent) => {
      point = axis === "x" ? e.clientX : e.clientY;
      if (frame === undefined) frame = requestAnimationFrame(apply);
    };
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      // Land the last move before the drag is declared over, so the settled
      // size — the one that gets persisted — is the one under the pointer.
      if (frame !== undefined) {
        cancelAnimationFrame(frame);
        apply();
      }
      setIsResizing(false);
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
    window.addEventListener("blur", finish);
    // Lock the cursor + suppress text selection window-wide during the drag.
    const prevCursor = document.body.style.cursor;
    const prevSelect = document.body.style.userSelect;
    document.body.style.cursor = axis === "x" ? "col-resize" : "row-resize";
    document.body.style.userSelect = "none";

    return () => {
      if (frame !== undefined) cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      window.removeEventListener("blur", finish);
      document.body.style.cursor = prevCursor;
      document.body.style.userSelect = prevSelect;
    };
  }, [isResizing, min, max, axis, handle]);

  // Persist only the settled size (on mount + after each drag ends), not every
  // intermediate drag frame — avoids hammering localStorage on pointermove.
  useEffect(() => {
    if (isResizing) return;
    try {
      window.localStorage.setItem(storageKey, String(size));
    } catch {
      /* quota / private mode — pane size is non-critical, ignore */
    }
  }, [size, isResizing, storageKey]);

  // Keep the stored preference in state while returning only the size that
  // fits the current measured frame. Merely narrowing a window must not erase
  // the user's wider layout preference.
  const visibleSize = clampSize(size, min, max);
  return { size: visibleSize, isResizing, startResize, reset, nudge, resize };
}

function loadSize(key: string, fallback: number): number {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed) ? parsed : fallback;
  } catch {
    return fallback;
  }
}
