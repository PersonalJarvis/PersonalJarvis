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
 * Share of a pane's WIDTH, down its middle, where a drop stacks rather than
 * lands beside — the column the two vertical zones live in.
 *
 * The two vertical zones used to be BANDS along the top and bottom edges
 * instead, and that shape cannot be made to work at both of the sizes a pane
 * really has. A band deep enough to aim at in a 900 px column swallows the
 * sideways drag that crosses it (BUG-111); one shallow enough not to —
 * `EDGE_MAX_PX` was 88 — is under a tenth of that column's height, and reported
 * on 2026-08-03 as "you cannot drag terminals underneath each other at all".
 * Both complaints are true of the same geometry, so the geometry is what
 * changed.
 *
 * A stripe answers both because it divides the axis the pane does NOT vary
 * much on. Panes get taller and shorter as the grid fills; a column's width
 * stays near `MIN_PANE_WIDTH_PX` whatever happens below it. So the stripe is a
 * target of roughly constant size, and — the part the bands could never give —
 * it runs the pane's FULL height: anywhere down the middle is "stack it", with
 * the half the pointer is in saying above or below.
 *
 * Half the width, so aiming for a side and aiming for the middle cost the same.
 */
export const STACK_STRIPE_FRACTION = 0.5;

/** Pixels the pointer must travel before a click on a header becomes a drag. */
export const DRAG_THRESHOLD_PX = 5;

/** The part of a rectangle this module needs — `DOMRect` satisfies it. */
export interface PaneRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Anything that changes what a drop means without moving the pointer. */
export interface ZoneOptions {
  /** The swap modifier is held — every drop becomes an exchange. */
  swap?: boolean;
  /** Share of the width the stack-it stripe may claim. Test seam. */
  stripe?: number;
}

/**
 * Which drop a point inside ``rect`` means.
 *
 * **Dragging MOVES a pane; it does not exchange two.** That is the whole shape
 * of this function, and it was learned the hard way: carrying a pane sideways
 * onto another one is how a person says "put it over there", and answering that
 * with a swap sends the target back the other way — the user asked for one pane
 * to move and two of them did (BUG-111). Exchanging two panes is still worth
 * having — it is the only move that leaves the grid's shape untouched — so it
 * stays on the swap modifier, where it cannot happen to someone who did not ask
 * for it.
 *
 * What is left is read as a picture of the result, which is what a person
 * dropping something believes they are doing: **the pane lands where they are
 * pointing.** A pane dropped down the middle of the target shares that column,
 * so the target's own space splits horizontally and the vertical half under the
 * pointer says which of the two the moved pane takes. A pane dropped out on
 * either flank becomes a column of its own on that side.
 *
 * The middle is a full-height STRIPE rather than two edge bands (see
 * `STACK_STRIPE_FRACTION`), and that is the difference that makes stacking
 * reachable: the flanks still take a drop at any height, so carrying a pane
 * sideways across a tall column never stacks it by accident, while aiming for
 * the middle no longer means hitting a strip a tenth of the pane deep.
 *
 * Every point in the pane is still a landing place — there is no dead middle
 * and nothing to hit exactly.
 */
export function zoneFor(
  rect: PaneRect,
  x: number,
  y: number,
  options: ZoneOptions = {},
): DropZone {
  // A pane with no measurable box (hidden behind a maximized sibling, or not
  // laid out yet) can still be pointed at in theory. Swap is the answer that
  // cannot produce a nonsensical layout, so it is the fallback.
  if (rect.width <= 0 || rect.height <= 0) return "swap";
  if (options.swap) return "swap";
  const stripe = rect.width * (options.stripe ?? STACK_STRIPE_FRACTION);
  const fromCentre = Math.abs(x - (rect.left + rect.width / 2));
  if (fromCentre <= stripe / 2) {
    return y - rect.top < rect.height / 2 ? "above" : "below";
  }
  return x - rect.left < rect.width / 2 ? "left" : "right";
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
  options: ZoneOptions = {},
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
      return { target: candidate.name, zone: zoneFor(rect, x, y, options) };
    }
  }
  return null;
}

/**
 * The key that turns a move into an exchange, held during the drag.
 *
 * Shift rather than Alt: on Windows a bare Alt press is the window menu's own
 * shortcut, so letting go of it mid-drag pulls focus out of the page.
 */
export const SWAP_MODIFIER = "Shift";

export interface PaneArrange {
  /** Call-sign of the pane currently in hand, or null when nothing is held. */
  held: string | null;
  /** The pane under the cursor and what a drop there would do. */
  hover: ArrangeHover | null;
  /** Viewport coordinates of the cursor, for the label that follows it. */
  point: { x: number; y: number } | null;
  /** Whether the swap modifier is down, so the label can say what it changes. */
  swapping: boolean;
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
  // The last place the pointer was, so pressing or releasing the swap modifier
  // can re-answer "what would a drop here do" without the pointer moving.
  const pointRef = useRef<{ x: number; y: number } | null>(null);
  const swapRef = useRef(false);
  const onDropRef = useRef(onDrop);
  onDropRef.current = onDrop;

  const [held, setHeld] = useState<string | null>(null);
  const [hover, setHover] = useState<ArrangeHover | null>(null);
  const [point, setPoint] = useState<{ x: number; y: number } | null>(null);
  const [swapping, setSwapping] = useState(false);

  const finish = useCallback((commit: boolean) => {
    const moved = heldRef.current;
    const drop = hoverRef.current;
    cleanup.current?.();
    cleanup.current = null;
    pending.current = null;
    heldRef.current = null;
    hoverRef.current = null;
    pointRef.current = null;
    swapRef.current = false;
    setHeld(null);
    setHover(null);
    setPoint(null);
    setSwapping(false);
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
      swapRef.current = event.shiftKey;

      /** Re-read what a drop would mean, from wherever the pointer already is. */
      const reread = () => {
        const at = pointRef.current;
        if (at === null || heldRef.current === null) return;
        const next = pickTarget(measure(), heldRef.current, at.x, at.y, {
          swap: swapRef.current,
        });
        hoverRef.current = next;
        setHover(next);
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
        pointRef.current = { x: moveEvent.clientX, y: moveEvent.clientY };
        setPoint(pointRef.current);
        // Read from the event rather than trusting the key listeners alone: a
        // modifier pressed or released while the window was in the background
        // never fired one, and the pointer event always carries the truth.
        if (moveEvent.shiftKey !== swapRef.current) {
          swapRef.current = moveEvent.shiftKey;
          setSwapping(moveEvent.shiftKey);
        }
        reread();
      };

      const onUp = (upEvent: globalThis.PointerEvent) => {
        if (upEvent.pointerId !== pending.current?.pointerId) return;
        finish(true);
      };
      const onCancel = () => finish(false);
      const onKey = (keyEvent: globalThis.KeyboardEvent) => {
        if (keyEvent.key === "Escape") {
          finish(false);
          return;
        }
        if (keyEvent.key === SWAP_MODIFIER && !swapRef.current) {
          swapRef.current = true;
          setSwapping(true);
          reread();
        }
      };
      const onKeyUp = (keyEvent: globalThis.KeyboardEvent) => {
        if (keyEvent.key === SWAP_MODIFIER && swapRef.current) {
          swapRef.current = false;
          setSwapping(false);
          reread();
        }
      };
      // Also a backstop: a pointerup delivered outside the window never
      // arrives, and a pane would otherwise stay stuck to the cursor.
      const onBlur = () => finish(false);

      window.addEventListener("pointermove", onMove, { passive: false });
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onCancel);
      window.addEventListener("keydown", onKey);
      window.addEventListener("keyup", onKeyUp);
      window.addEventListener("blur", onBlur);
      cleanup.current = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onCancel);
        window.removeEventListener("keydown", onKey);
        window.removeEventListener("keyup", onKeyUp);
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

  return { held, hover, point, swapping, start, registerCell };
}
