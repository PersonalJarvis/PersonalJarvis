/**
 * The workspace's own sizes: held while you drag them, remembered afterwards.
 *
 * The split tree the backend sends (`SessionState.layout`) carries both the
 * arrangement AND the weights, so sizes are no longer a browser-local
 * sidecar: a drag edits the local copy for the duration of the gesture and
 * then posts the result back (`saveLayoutWeights`). That is what lets sizes
 * survive an app restart on any machine, come back with a resumed workspace,
 * and stay one single authority — the localStorage weights this replaces
 * could disagree with the workspace whenever the backend reshaped it while
 * no grid was watching, and needed a whole remapping machinery to catch up.
 *
 * The backend, in turn, only ever ADOPTS weights into the shape it already
 * has, so a drag racing a voice-opened pane loses quietly and the next state
 * push snaps the grid to reality.
 *
 * ## Why a drag does not go through React
 *
 * `onPreview` is handed each frame's tree INSTEAD of state being written, and
 * state is written exactly once, when the pointer is released. A pointer
 * emits up to 120 moves a second, and every pane is a live terminal — routing
 * the gesture through state re-rendered the whole wall per move and dropped
 * the drag to a few frames a second (see the history in the retired
 * `usePaneWeights`). Painting the boxes directly costs one layout computation
 * and N style writes per frame and does not touch React at all.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { saveLayoutWeights } from "../../lib/agenticIdeApi";
import {
  dragSeam,
  evenSeam,
  evenedTree,
  sameShape,
  type LayoutNode,
  type PaneSeam,
} from "./treeLayout";

/** How far one arrow-key press moves a seam. Matches `PaneResizer`. */
const KEY_STEP_PX = 16;

/** How long after the last size edit the result is posted to the backend. */
const SAVE_DEBOUNCE_MS = 600;

/** How big the workspace currently is, in px, along each axis. */
export interface WorkspaceExtent {
  width: number;
  height: number;
}

export interface TreeSizeControls {
  /** The tree to lay out: the user's fresher local sizes, else the server's. */
  tree: LayoutNode | null;
  /** Id of the seam being dragged right now, if any. */
  dragging: string | null;
  /** Begin a drag — wire to a seam's `onPointerDown`. */
  startDrag: (seam: PaneSeam, event: React.PointerEvent) => void;
  /** Move a seam by ``deltaPx`` — the keyboard equivalent of a drag. */
  nudge: (seam: PaneSeam, deltaPx: number) => void;
  /** Give a seam's two neighbours the same size — wire to `onDoubleClick`. */
  even: (seam: PaneSeam) => void;
  /** Every pane back to an equal share — the toolbar's "even out". */
  evenAll: () => void;
  /**
   * The tree a drag is currently at, or null when nothing is being dragged.
   * How a caller that repaints mid-gesture recovers the in-flight sizes.
   */
  liveTree: React.MutableRefObject<LayoutNode | null>;
}

/**
 * Drag state for every seam in one workspace.
 *
 * `serverTree` is the layout the session state carries; `extent` is read as a
 * function because the workspace can be resized mid-drag and the
 * pixels-to-weight conversion has to use the size the seam is actually being
 * dragged across.
 */
export function useTreeSizes(
  serverTree: LayoutNode | null | undefined,
  extent: () => WorkspaceExtent,
  onPreview?: (tree: LayoutNode) => void,
): TreeSizeControls {
  /*
   * The local override holds sizes the user chose that the server has not
   * echoed back yet. It stays in force exactly as long as the server's tree
   * keeps the shape it was dragged against: any structural change — a split,
   * a close, a move, another client — wins immediately, because a stale
   * override laid over a reshaped workspace would place panes that no longer
   * exist.
   */
  const [override, setOverride] = useState<LayoutNode | null>(null);
  const tree =
    override && sameShape(override, serverTree ?? null)
      ? override
      : (serverTree ?? null);

  const [dragging, setDragging] = useState<string | null>(null);

  /*
   * Saves are debounced and fire-and-forget: a run of arrow-key nudges is one
   * write, and a failed write costs nothing but persistence — the sizes stay
   * on screen from the override, and the next successful edit re-sends the
   * whole tree anyway.
   */
  const pendingSave = useRef<LayoutNode | null>(null);
  const saveTimer = useRef<number | undefined>(undefined);
  const flushSave = useCallback(() => {
    if (saveTimer.current !== undefined) {
      window.clearTimeout(saveTimer.current);
      saveTimer.current = undefined;
    }
    const unsaved = pendingSave.current;
    pendingSave.current = null;
    if (!unsaved) return;
    saveLayoutWeights(unsaved).catch((error) => {
      console.warn("Agentic IDE: pane sizes were not persisted:", error);
    });
  }, []);
  const commit = useCallback(
    (next: LayoutNode) => {
      setOverride(next);
      pendingSave.current = next;
      if (saveTimer.current !== undefined) window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(flushSave, SAVE_DEBOUNCE_MS);
    },
    [flushSave],
  );
  // An unmount (switching workspaces, closing the view) must not eat the last
  // gesture — post whatever is still waiting on the way out.
  useEffect(() => flushSave, [flushSave]);

  // Drag anchors — refs so the move handler never reads a stale closure. The
  // drag works from the tree it STARTED with, so a slow pointer cannot
  // accumulate rounding drift over a hundred frames.
  const active = useRef<{ seam: PaneSeam; point: number; from: LayoutNode } | null>(
    null,
  );
  const point = useRef(0);
  const live = useRef<LayoutNode | null>(null);

  const treeRef = useRef(tree);
  treeRef.current = tree;
  const extentRef = useRef(extent);
  extentRef.current = extent;
  const previewRef = useRef(onPreview);
  previewRef.current = onPreview;

  const startDrag = useCallback((seam: PaneSeam, event: React.PointerEvent) => {
    const from = treeRef.current;
    if (!from) return;
    event.preventDefault();
    const at = seam.orientation === "vertical" ? event.clientX : event.clientY;
    active.current = { seam, point: at, from };
    point.current = at;
    live.current = null;
    setDragging(seam.id);
  }, []);

  useEffect(() => {
    if (!dragging) return;

    const axisPx = (seam: PaneSeam) => {
      const size = extentRef.current();
      return seam.orientation === "vertical" ? size.width : size.height;
    };

    // One computation and one paint per FRAME, however many moves the pointer
    // delivered in it.
    let frame: number | undefined;
    const apply = () => {
      frame = undefined;
      const drag = active.current;
      if (!drag) return;
      const next = dragSeam(
        drag.from,
        drag.seam,
        point.current - drag.point,
        axisPx(drag.seam),
      );
      live.current = next;
      if (previewRef.current) previewRef.current(next);
      else commit(next);
    };

    const onMove = (event: PointerEvent) => {
      const drag = active.current;
      if (!drag) return;
      point.current =
        drag.seam.orientation === "vertical" ? event.clientX : event.clientY;
      if (frame === undefined) frame = requestAnimationFrame(apply);
    };
    const settle = () => {
      // Land the last move before committing, so the sizes React is told
      // about are the ones already on screen.
      if (frame !== undefined) {
        cancelAnimationFrame(frame);
        apply();
      }
      const settled = live.current;
      active.current = null;
      live.current = null;
      setDragging(null);
      if (settled) commit(settled);
    };
    const onUp = () => settle();
    // A cancelled pointer (a touch taken over by the browser, a window that
    // lost the device) must commit too: the boxes are already painted at the
    // dragged sizes.
    const onCancel = () => settle();

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
    // Lock the cursor and suppress text selection for the whole drag, so the
    // pointer can wander off the 6px grip without the drag looking broken.
    const previousCursor = document.body.style.cursor;
    const previousSelect = document.body.style.userSelect;
    document.body.style.cursor =
      active.current?.seam.orientation === "vertical" ? "col-resize" : "row-resize";
    document.body.style.userSelect = "none";

    return () => {
      if (frame !== undefined) cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousSelect;
    };
  }, [commit, dragging]);

  const nudge = useCallback(
    (seam: PaneSeam, deltaPx: number) => {
      const current = treeRef.current;
      if (!current) return;
      const size = extentRef.current();
      const axis = seam.orientation === "vertical" ? size.width : size.height;
      commit(dragSeam(current, seam, deltaPx, axis));
    },
    [commit],
  );

  const even = useCallback(
    (seam: PaneSeam) => {
      const current = treeRef.current;
      if (!current) return;
      commit(evenSeam(current, seam));
    },
    [commit],
  );

  const evenAll = useCallback(() => {
    const current = treeRef.current;
    if (!current) return;
    commit(evenedTree(current));
  }, [commit]);

  return { tree, dragging, startDrag, nudge, even, evenAll, liveTree: live };
}

export { KEY_STEP_PX };
