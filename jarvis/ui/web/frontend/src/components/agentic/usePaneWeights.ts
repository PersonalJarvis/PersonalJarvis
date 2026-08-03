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

/**
 * Where each workspace's pane→column arrangement is remembered.
 *
 * Stored beside the weights because the two are one fact: column weights are
 * keyed by column INDEX, so they only mean anything together with the mapping
 * from panes to those indexes. A workspace reopened after the backend
 * rearranged it (a restart, a voice-opened terminal, another client) reads
 * this to carry every dragged width over to the pane that owned it, instead of
 * letting index-keyed widths land on whichever pane holds that index now.
 */
const ARRANGEMENT_PREFIX = "jarvis.agenticIde.paneArrangement.v1.";

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
    // Only the families that still exist are read back. Entries written by an
    // older build carry a `bands` list too — the workspace no longer wraps into
    // bands, so it is dropped here rather than kept as a number nothing means.
    return {
      columns: Array.isArray(parsed.columns) ? parsed.columns.map(Number) : [],
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

/** The pane→column arrangement the stored weights are keyed to, if one was saved. */
export function loadStoredArrangement(
  workspaceId: string,
): Record<string, number> | null {
  try {
    const raw = window.localStorage.getItem(ARRANGEMENT_PREFIX + workspaceId);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const entries = Object.entries(parsed).filter(
      (entry): entry is [string, number] =>
        Number.isFinite(entry[1]) && (entry[1] as number) >= 0,
    );
    return entries.length > 0 ? Object.fromEntries(entries) : null;
  } catch {
    // Corrupt entry, private mode, storage disabled — without a remembered
    // arrangement the weights are simply trusted as they are, which is the
    // exact behaviour every workspace had before arrangements were stored.
    return null;
  }
}

/** Remember which panes the currently stored weights belong to. */
export function saveStoredArrangement(
  workspaceId: string,
  arrangement: Record<string, number>,
): void {
  try {
    window.localStorage.setItem(
      ARRANGEMENT_PREFIX + workspaceId,
      JSON.stringify(arrangement),
    );
  } catch {
    /* quota / private mode — the arrangement will not survive this session */
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
  /**
   * The weights a drag is currently at, or null when nothing is being dragged.
   *
   * They are NOT in `weights` on purpose (see `onPreview`) — this is how a
   * caller that repaints during a drag recovers the in-flight sizes if React
   * renders for some unrelated reason mid-gesture and puts the settled ones
   * back on screen.
   */
  liveWeights: React.MutableRefObject<PaneWeights | null>;
}

/**
 * Drag state for every seam in one workspace.
 *
 * `extent` is read as a function rather than taken as a value because the
 * workspace can be resized mid-drag (a window animation, the prompt bar opening)
 * and the pixels-to-weight conversion has to use the size the seam is actually
 * being dragged across.
 *
 * ## Why a drag does not go through React
 *
 * `onPreview` is handed each frame's weights INSTEAD of them being put into
 * state, and state is written exactly once, when the pointer is released.
 *
 * The reason is what a workspace re-render costs. Every pane in it is a live
 * terminal with a header, a recap card and a scrollbar, and a pointer emits up
 * to 120 moves a second — so routing a drag through state re-built the entire
 * grid on every one of them, and a dozen panes dropped the gesture to a few
 * frames a second. Worse, it fed back: each of those renders resized every
 * pane's DOM box, which woke each pane's own ResizeObserver, which eventually
 * refitted the terminal and told the agent behind it to redraw its whole screen
 * — mid-drag, on the same thread. Painting the boxes directly costs one layout
 * computation and N style writes per frame and does not touch React at all.
 *
 * A caller that passes nothing keeps the old behaviour (state per frame), which
 * is what the keyboard path and the hook's own tests use.
 */
export function usePaneWeights(
  workspaceId: string,
  extent: () => WorkspaceExtent,
  onPreview?: (weights: PaneWeights) => void,
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

  /** Where the pointer is right now, and the sizes that produces. */
  const point = useRef(0);
  const live = useRef<PaneWeights | null>(null);

  const weightsRef = useRef(weights);
  weightsRef.current = weights;

  const extentRef = useRef(extent);
  extentRef.current = extent;

  const previewRef = useRef(onPreview);
  previewRef.current = onPreview;

  const startDrag = useCallback((seam: PaneSeam, event: React.PointerEvent) => {
    event.preventDefault();
    const at = seam.orientation === "vertical" ? event.clientX : event.clientY;
    active.current = { seam, point: at, from: weightsRef.current };
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
    // delivered in it. The workspace is measured here too, rather than per
    // move, so a drag reads the layout once a frame instead of interleaving
    // reads and writes — which is what makes a browser recompute the whole
    // grid several times over between two frames.
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
      // No preview target (the keyboard path, the hook's own tests): fall back
      // to state, which is the behaviour every caller had before.
      if (previewRef.current) previewRef.current(next);
      else setWeights(next);
    };

    const onMove = (event: PointerEvent) => {
      const drag = active.current;
      if (!drag) return;
      point.current =
        drag.seam.orientation === "vertical" ? event.clientX : event.clientY;
      if (frame === undefined) frame = requestAnimationFrame(apply);
    };
    const settle = () => {
      // Land the last move before committing, so the sizes React is told about
      // are the ones already on screen — otherwise a drag that ended between
      // two frames would commit the frame before it and visibly snap back.
      if (frame !== undefined) {
        cancelAnimationFrame(frame);
        apply();
      }
      const settled = live.current;
      active.current = null;
      live.current = null;
      setDragging(null);
      if (settled) setWeights(settled);
    };
    const onUp = () => settle();
    // A cancelled pointer (a touch taken over by the browser, a window that
    // lost the device) must commit too: the boxes are already painted at the
    // dragged sizes, and leaving state behind them would put every pane back
    // where it started on the next unrelated render.
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
  }, [dragging]);

  const nudge = useCallback((seam: PaneSeam, deltaPx: number) => {
    const size = extentRef.current();
    const axis = seam.orientation === "vertical" ? size.width : size.height;
    setWeights((current) => dragSeam(current, seam, deltaPx, axis));
  }, []);

  const even = useCallback((seam: PaneSeam) => {
    setWeights((current) => evenSeam(current, seam));
  }, []);

  return { weights, setWeights, dragging, startDrag, nudge, even, liveWeights: live };
}

export { KEY_STEP_PX };
