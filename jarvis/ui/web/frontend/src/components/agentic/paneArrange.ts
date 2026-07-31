/**
 * Picking a terminal up by its header and putting it somewhere else.
 *
 * A workspace is assembled one split at a time, so it ends up in whatever order
 * the splits happened rather than the order the work is in. Until now the only
 * way to fix that was to close a pane and open a new one somewhere else — which
 * kills a working coding agent and its whole conversation. Dragging moves the
 * pane instead: the same process, the same socket, two different numbers.
 *
 * ## Why pointer events and not HTML5 drag-and-drop
 *
 * The panes are already a drop target for FILES (see ./paneFileDrag), and the
 * browser gives a page exactly one drag protocol — a second one over the same
 * elements means every `dragover` has to decide which of the two gestures it is
 * looking at, on a `DataTransfer` that deliberately hides its payload until the
 * drop. Pointer events are a separate channel entirely, so dropping a screenshot
 * onto a pane and dropping a PANE onto a pane can never be confused for one
 * another. They also work with touch and pen for free, and let this file own the
 * ghost and the highlight rather than negotiating a drag image with the browser.
 *
 * ## Why a threshold before the drag arms
 *
 * The header is also where the user clicks to focus a pane, and a mouse moves a
 * pixel or two during an ordinary click. Arming immediately would turn every
 * click on a header into a drag that lands back where it started — visually a
 * flicker, and one mis-drop away from rearranging the grid by accident.
 */
import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

/** Where a drop means the pane should go, relative to the pane under the cursor. */
export type DropZone = "swap" | "left" | "right" | "above" | "below";

/**
 * The most of a pane's width/height one edge zone may claim.
 *
 * A share on its own was the whole of it once, and it made the common gesture
 * unreliable: a workspace of five panes draws each one tall and narrow, so 0.3
 * of the HEIGHT is a band far deeper than 0.3 of the width is wide, and the two
 * horizontal bands together swallowed most of the pane. Carrying a pane sideways
 * onto its neighbour — the ordinary "these two are the wrong way round" — then
 * landed in `below` and stacked the pane under the target instead of exchanging
 * the two, collapsing a column of the grid on the way (BUG-111).
 */
export const EDGE_FRACTION = 0.3;

/**
 * The ceiling that keeps an edge zone from growing with the pane.
 *
 * An edge is a place a user AIMS at, and aiming does not get harder because the
 * pane got taller — so past a point the band stops growing and the middle takes
 * the rest. That restores what the share was always meant to buy: swap is the
 * biggest target by a wide margin, because it is the move people actually reach
 * for and the only one that leaves the rest of the grid exactly as it was, while
 * the four edges stay wide enough to hit without pixel accuracy.
 */
export const EDGE_MAX_PX = 88;

/** Pixels the pointer must travel before a click on a header becomes a drag. */
export const DRAG_THRESHOLD_PX = 5;

/** The part of a rectangle this module needs — `DOMRect` satisfies it. */
export interface PaneRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * Which drop a point inside ``rect`` means.
 *
 * Every distance here is in PIXELS, and each edge is judged against its own
 * band. Comparing shares instead — how far across the pane, rather than how far
 * from the edge — reads a tall narrow pane as mostly top-and-bottom, so a point
 * halfway down the middle of a grid column came out as `below` (BUG-111). A
 * point belongs to an edge only when it is inside THAT edge's band; when it is
 * inside none of them it is in the middle, which is a swap.
 */
export function zoneFor(
  rect: PaneRect,
  x: number,
  y: number,
  edge: number = EDGE_FRACTION,
): DropZone {
  // A pane with no measurable box (hidden behind a maximized sibling, or not
  // laid out yet) can still be pointed at in theory. Swap is the answer that
  // cannot produce a nonsensical layout, so it is the fallback.
  if (rect.width <= 0 || rect.height <= 0) return "swap";
  const bandX = Math.min(rect.width * edge, EDGE_MAX_PX);
  const bandY = Math.min(rect.height * edge, EDGE_MAX_PX);
  const sides: Array<[DropZone, number, number]> = [
    ["left", x - rect.left, bandX],
    ["right", rect.left + rect.width - x, bandX],
    ["above", y - rect.top, bandY],
    ["below", rect.top + rect.height - y, bandY],
  ];
  // Among the bands the point is actually in, the nearest edge wins — which is
  // what resolves the corners: a point in the top-left corner belongs to
  // whichever of the two edges it is nearer to.
  let best: [DropZone, number, number] | null = null;
  for (const side of sides) {
    if (side[1] > side[2]) continue;
    if (best === null || side[1] < best[1]) best = side;
  }
  return best === null ? "swap" : best[0];
}

/** A pane the drag can be dropped on, as measured right now. */
export interface PaneTarget {
  name: string;
  rect: PaneRect;
}

/** What the pointer is currently over, if it is over anything droppable. */
export interface ArrangeHover {
  target: string;
  zone: DropZone;
}

/**
 * The pane under ``(x, y)`` and what dropping there would mean.
 *
 * The held pane is skipped rather than returning "you are over yourself":
 * hovering the pane in your hand is not a drop, and offering one would put a
 * highlight on the thing that is being dragged.
 */
export function pickTarget(
  targets: readonly PaneTarget[],
  held: string | null,
  x: number,
  y: number,
): ArrangeHover | null {
  for (const candidate of targets) {
    if (candidate.name === held) continue;
    const { rect } = candidate;
    if (rect.width <= 0 || rect.height <= 0) continue;
    if (
      x >= rect.left &&
      x <= rect.left + rect.width &&
      y >= rect.top &&
      y <= rect.top + rect.height
    ) {
      return { target: candidate.name, zone: zoneFor(rect, x, y) };
    }
  }
  return null;
}

export interface PaneArrange {
  /** Call-sign of the pane currently in hand, or null when nothing is held. */
  held: string | null;
  /** The pane under the cursor and what a drop there would do. */
  hover: ArrangeHover | null;
  /** Viewport coordinates of the cursor, for the label that follows it. */
  point: { x: number; y: number } | null;
  /** Start a drag — wired to the pane header's `pointerdown`. */
  start: (name: string, event: ReactPointerEvent) => void;
  /** Ref callback that lets a pane cell be measured as a drop target. */
  registerCell: (name: string) => (element: HTMLElement | null) => void;
}

/**
 * Track one pane drag from the header press to the drop.
 *
 * ``onDrop`` runs once, with the pane that was carried, the pane it was dropped
 * on and what that drop meant. A drag that ends on nothing, on the pane itself,
 * or on Escape reports nothing at all.
 */
export function usePaneArrange(
  onDrop: (moved: string, target: string, zone: DropZone) => void,
): PaneArrange {
  const cells = useRef(new Map<string, HTMLElement>());
  // One stable ref callback per pane. A fresh closure each render would make
  // React detach and re-attach every cell on every render, so a pane could be
  // unregistered at the exact moment a drag measures it.
  const refCallbacks = useRef(new Map<string, (element: HTMLElement | null) => void>());
  const pending = useRef<{ name: string; pointerId: number; x: number; y: number } | null>(
    null,
  );
  const cleanup = useRef<(() => void) | null>(null);
  // Mirrors of the state below, so the pointer handlers read the current values
  // without being re-created (and re-registered) on every move.
  const heldRef = useRef<string | null>(null);
  const hoverRef = useRef<ArrangeHover | null>(null);
  const onDropRef = useRef(onDrop);
  onDropRef.current = onDrop;

  const [held, setHeld] = useState<string | null>(null);
  const [hover, setHover] = useState<ArrangeHover | null>(null);
  const [point, setPoint] = useState<{ x: number; y: number } | null>(null);

  const finish = useCallback((commit: boolean) => {
    const moved = heldRef.current;
    const drop = hoverRef.current;
    cleanup.current?.();
    cleanup.current = null;
    pending.current = null;
    heldRef.current = null;
    hoverRef.current = null;
    setHeld(null);
    setHover(null);
    setPoint(null);
    // `moved` is null for a press that never crossed the threshold — an
    // ordinary click on the header, which must stay an ordinary click.
    if (commit && moved && drop && drop.target !== moved) {
      onDropRef.current(moved, drop.target, drop.zone);
    }
  }, []);

  const measure = useCallback((): PaneTarget[] => {
    const rows: PaneTarget[] = [];
    for (const [name, element] of cells.current) {
      rows.push({ name, rect: element.getBoundingClientRect() });
    }
    return rows;
  }, []);

  const start = useCallback(
    (name: string, event: ReactPointerEvent) => {
      // Left button only. The right one opens the app's own context menu, and
      // the middle one is a paste on some platforms — neither is a drag.
      if (event.button !== 0) return;
      // A drag already in flight (a second finger, a stuck pointer) is left
      // alone rather than replaced halfway through.
      if (pending.current) return;
      pending.current = {
        name,
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
      };

      const onMove = (moveEvent: globalThis.PointerEvent) => {
        const origin = pending.current;
        if (!origin || moveEvent.pointerId !== origin.pointerId) return;
        if (heldRef.current === null) {
          const travelled = Math.hypot(
            moveEvent.clientX - origin.x,
            moveEvent.clientY - origin.y,
          );
          if (travelled < DRAG_THRESHOLD_PX) return;
          heldRef.current = origin.name;
          setHeld(origin.name);
        }
        // Stops the browser from turning the drag into a text selection that
        // sweeps across every pane it crosses.
        moveEvent.preventDefault();
        setPoint({ x: moveEvent.clientX, y: moveEvent.clientY });
        const next = pickTarget(
          measure(),
          heldRef.current,
          moveEvent.clientX,
          moveEvent.clientY,
        );
        hoverRef.current = next;
        setHover(next);
      };

      const onUp = (upEvent: globalThis.PointerEvent) => {
        if (upEvent.pointerId !== pending.current?.pointerId) return;
        finish(true);
      };
      const onCancel = () => finish(false);
      const onKey = (keyEvent: globalThis.KeyboardEvent) => {
        if (keyEvent.key === "Escape") finish(false);
      };
      // Also a backstop: a pointerup delivered outside the window never
      // arrives, and a pane would otherwise stay stuck to the cursor.
      const onBlur = () => finish(false);

      window.addEventListener("pointermove", onMove, { passive: false });
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onCancel);
      window.addEventListener("keydown", onKey);
      window.addEventListener("blur", onBlur);
      cleanup.current = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onCancel);
        window.removeEventListener("keydown", onKey);
        window.removeEventListener("blur", onBlur);
      };
    },
    [finish, measure],
  );

  // A workspace closed mid-drag would leave the listeners behind.
  useEffect(() => () => cleanup.current?.(), []);

  const registerCell = useCallback((name: string) => {
    const existing = refCallbacks.current.get(name);
    if (existing) return existing;
    const callback = (element: HTMLElement | null) => {
      if (element) cells.current.set(name, element);
      else cells.current.delete(name);
    };
    refCallbacks.current.set(name, callback);
    return callback;
  }, []);

  return { held, hover, point, start, registerCell };
}
