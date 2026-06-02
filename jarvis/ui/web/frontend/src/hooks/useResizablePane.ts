import { useCallback, useEffect, useRef, useState } from "react";

export interface ResizablePaneOptions {
  /** localStorage key the settled width is persisted under. */
  storageKey: string;
  /** Width used when nothing is stored yet (px). */
  defaultWidth: number;
  /** Lower bound (px) — the pane can never get thinner than this. */
  min: number;
  /** Upper bound (px) — keeps the pane from swallowing its neighbour. */
  max: number;
}

export interface ResizablePane {
  /** Current pane width in px (already clamped). */
  width: number;
  /** True while the user is actively dragging the grip. */
  isResizing: boolean;
  /** Attach to the grip's ``onPointerDown`` to begin a drag. */
  startResize: (e: React.PointerEvent) => void;
  /** Snap back to ``defaultWidth`` (wire to the grip's ``onDoubleClick``). */
  reset: () => void;
}

/**
 * Clamp a pixel width into the ``[min, max]`` band and round to a whole pixel.
 *
 * Pulled out as a free function so the boundary maths is unit-testable without
 * a DOM — the drag interaction itself is verified live in the app.
 */
export function clampWidth(value: number, min: number, max: number): number {
  if (Number.isNaN(value)) return min;
  return Math.min(max, Math.max(min, Math.round(value)));
}

/**
 * Drag-to-resize state for a horizontal splitter pane.
 *
 * The settled width is persisted to ``localStorage`` so the layout survives a
 * reload. Pointer listeners live on ``window`` (not the thin grip) for the
 * whole drag, which is what lets the cursor wander off the 6px handle without
 * dropping the drag — the canonical splitter behaviour.
 */
export function useResizablePane({
  storageKey,
  defaultWidth,
  min,
  max,
}: ResizablePaneOptions): ResizablePane {
  const [width, setWidth] = useState<number>(() =>
    clampWidth(loadWidth(storageKey, defaultWidth), min, max),
  );
  const [isResizing, setIsResizing] = useState(false);

  // Drag anchors — refs so the move handler never reads a stale closure.
  const startX = useRef(0);
  const startWidth = useRef(width);

  const startResize = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      startX.current = e.clientX;
      startWidth.current = width;
      setIsResizing(true);
    },
    [width],
  );

  const reset = useCallback(() => setWidth(defaultWidth), [defaultWidth]);

  // Global pointer listeners are armed only while dragging.
  useEffect(() => {
    if (!isResizing) return;

    const onMove = (e: PointerEvent) => {
      setWidth(clampWidth(startWidth.current + (e.clientX - startX.current), min, max));
    };
    const onUp = () => setIsResizing(false);

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    // Lock the cursor + suppress text selection window-wide during the drag.
    const prevCursor = document.body.style.cursor;
    const prevSelect = document.body.style.userSelect;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.cursor = prevCursor;
      document.body.style.userSelect = prevSelect;
    };
  }, [isResizing, min, max]);

  // Persist only the settled width (on mount + after each drag ends), not every
  // intermediate drag frame — avoids hammering localStorage on pointermove.
  useEffect(() => {
    if (isResizing) return;
    try {
      window.localStorage.setItem(storageKey, String(width));
    } catch {
      /* quota / private mode — pane width is non-critical, ignore */
    }
  }, [width, isResizing, storageKey]);

  return { width, isResizing, startResize, reset };
}

function loadWidth(key: string, fallback: number): number {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed) ? parsed : fallback;
  } catch {
    return fallback;
  }
}
