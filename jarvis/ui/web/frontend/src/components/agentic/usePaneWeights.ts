/**
 * The workspace's own sizes: held while you drag them, remembered afterwards.
 *
 * Deliberately localStorage rather than the backend. How wide one person likes
 * a pane on a 4K monitor is a property of that screen, not of the workspace —
 * pushing it through the session state would make a laptop and a desktop fight
 * over one set of numbers, and cost a config write per drag frame. It sits
 * beside the terminal appearance and font size, which are stored the same way
 * for the same reason.
 *
 * The stored value is keyed by workspace, so switching tabs restores the sizes
 * that workspace was left at rather than inheriting the last one's.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  dragSeam,
  evenSeam,
  evenWeights,
  type PaneSeam,
  type PaneWeights,
} from "./paneLayout";

const STORAGE_PREFIX = "jarvis.agenticIde.paneWeights.v1.";

/** How far one arrow-key press moves a seam. Matches `PaneResizer`. */
const KEY_STEP_PX = 16;

function storageKey(workspaceId: string): string {
  return `${STORAGE_PREFIX}${workspaceId}`;
}

/** Read stored weights, tolerating anything that is not the shape we wrote. */
export function loadWeights(workspaceId: string): PaneWeights {
  try {
    const raw = window.localStorage.getItem(storageKey(workspaceId));
    if (!raw) return evenWeights();
    const parsed = JSON.parse(raw) as Partial<PaneWeights>;
    return {
      columns: Array.isArray(parsed.columns) ? parsed.columns.map(Number) : [],
      bands: Array.isArray(parsed.bands) ? parsed.bands.map(Number) : [],
      panes:
        parsed.panes && typeof parsed.panes === "object"
          ? (parsed.panes as Record<string, number>)
          : {},
    };
  } catch {
    // Corrupt entry, private mode, storage disabled — an even workspace is a
    // perfectly good workspace, and losing sizes must never cost the panes.
    return evenWeights();
  }
}

function saveWeights(workspaceId: string, weights: PaneWeights): void {
  try {
    window.localStorage.setItem(storageKey(workspaceId), JSON.stringify(weights));
  } catch {
    /* quota / private mode — sizes simply will not survive this session */
  }
}

/** How big the workspace currently is, in px, along each axis. */
export interface WorkspaceExtent {
  width: number;
  height: number;
}

export interface PaneWeightControls {
  weights: PaneWeights;
  /** Replace the weights — used when a split or a close rewrites them. */
  setWeights: (next: PaneWeights | ((current: PaneWeights) => PaneWeights)) => void;
  /** Id of the seam being dragged right now, if any. */
  dragging: string | null;
  /** Begin a drag — wire to a seam's `onPointerDown`. */
  startDrag: (seam: PaneSeam, event: React.PointerEvent) => void;
  /** Move a seam by ``deltaPx`` — the keyboard equivalent of a drag. */
  nudge: (seam: PaneSeam, deltaPx: number) => void;
  /** Give a seam's two neighbours the same size — wire to `onDoubleClick`. */
  even: (seam: PaneSeam) => void;
}

/**
 * Drag state for every seam in one workspace.
 *
 * `extent` is read as a function rather than taken as a value because the
 * workspace can be resized mid-drag (a window animation, the prompt bar opening)
 * and the pixels-to-weight conversion has to use the size the seam is actually
 * being dragged across.
 */
export function usePaneWeights(
  workspaceId: string,
  extent: () => WorkspaceExtent,
): PaneWeightControls {
  const [weights, setWeights] = useState<PaneWeights>(() => loadWeights(workspaceId));
  const [dragging, setDragging] = useState<string | null>(null);

  // Switching workspaces loads that workspace's sizes. Guarded by a ref so the
  // save effect below cannot write the outgoing workspace's weights under the
  // incoming one's key in the render between the two.
  const loadedFor = useRef(workspaceId);
  useEffect(() => {
    loadedFor.current = workspaceId;
    setWeights(loadWeights(workspaceId));
  }, [workspaceId]);

  useEffect(() => {
    if (dragging || loadedFor.current !== workspaceId) return;
    saveWeights(workspaceId, weights);
  }, [weights, dragging, workspaceId]);

  // Drag anchors — refs so the move handler never reads a stale closure. The
  // drag works from the weights it STARTED with, so a slow pointer cannot
  // accumulate rounding drift over a hundred frames.
  const active = useRef<{
    seam: PaneSeam;
    point: number;
    from: PaneWeights;
  } | null>(null);

  const weightsRef = useRef(weights);
  weightsRef.current = weights;

  const extentRef = useRef(extent);
  extentRef.current = extent;

  const startDrag = useCallback((seam: PaneSeam, event: React.PointerEvent) => {
    event.preventDefault();
    active.current = {
      seam,
      point: seam.orientation === "vertical" ? event.clientX : event.clientY,
      from: weightsRef.current,
    };
    setDragging(seam.id);
  }, []);

  useEffect(() => {
    if (!dragging) return;

    const axisPx = (seam: PaneSeam) => {
      const size = extentRef.current();
      return seam.orientation === "vertical" ? size.width : size.height;
    };

    const onMove = (event: PointerEvent) => {
      const drag = active.current;
      if (!drag) return;
      const now = drag.seam.orientation === "vertical" ? event.clientX : event.clientY;
      setWeights(dragSeam(drag.from, drag.seam, now - drag.point, axisPx(drag.seam)));
    };
    const onUp = () => {
      active.current = null;
      setDragging(null);
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    // Lock the cursor and suppress text selection for the whole drag, so the
    // pointer can wander off the 6px grip without the drag looking broken.
    const previousCursor = document.body.style.cursor;
    const previousSelect = document.body.style.userSelect;
    document.body.style.cursor =
      active.current?.seam.orientation === "vertical" ? "col-resize" : "row-resize";
    document.body.style.userSelect = "none";

    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousSelect;
    };
  }, [dragging]);

  const nudge = useCallback((seam: PaneSeam, deltaPx: number) => {
    const size = extentRef.current();
    const axis = seam.orientation === "vertical" ? size.width : size.height;
    setWeights((current) => dragSeam(current, seam, deltaPx, axis));
  }, []);

  const even = useCallback((seam: PaneSeam) => {
    setWeights((current) => evenSeam(current, seam));
  }, []);

  return { weights, setWeights, dragging, startDrag, nudge, even };
}

export { KEY_STEP_PX };
