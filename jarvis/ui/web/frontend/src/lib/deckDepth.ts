import type { BoardSlot } from "@/lib/deckStandby";

/**
 * The board's depth — the "Schaukasten" (display case).
 *
 * The mission deck used to be a flat grid of instruments on top of the
 * wallpaper (maintainer, 2026-08-22: it does not look good, it is not creative).
 * The wallpaper stays exactly what it is — the back wall — and the board
 * gains DEPTH in front of it instead: every slot stands on its own plane at
 * a distance from the wall, turned a few degrees toward the centre, and the
 * whole case sways a little with the pointer so the eye reads the distances.
 * Nothing is scenery for its own sake: near is what the person acts on most
 * (log, terminals, outputs), far is what is looked at (the memory map, the
 * capture), and the centre with the mascot sits ON the wall's plane so the
 * orb's shared-layout travel from the start sequence still lands where it
 * measured.
 *
 * Pure numbers, no React: MissionDeckView turns them into CSS custom
 * properties, index.css turns those into one `transform`. The parallax is
 * a transform too, so it runs off the main thread.
 */

/** The perspective distance, in px — the camera's distance to the wall. */
export const DECK_PERSPECTIVE_PX = 1400;

/** How far the pointer moves the nearest plane, in px, edge to edge. */
export const PARALLAX_MAX_PX = 9;

export interface SlotDepth {
  /** Distance in front of (+) or behind (−) the wallpaper plane, in px. */
  z: number;
  /** Turn toward the centre, in degrees (left slots +, right slots −). */
  rotateY: number;
  /** Parallax factor: 1 = the full pointer travel, 0 = pinned to the wall. */
  parallax: number;
  /** Apparent size the slot should keep, so neighbours never overlap. */
  apparentScale: number;
}

/**
 * Where each slot stands. Near planes carry what the person acts on, far
 * planes what is looked at; the centre column is on the wall so the orb's
 * measured travel keeps landing on its mark.
 */
export const SLOT_DEPTH: Record<BoardSlot, SlotDepth> = {
  "left-top": { z: 70, rotateY: 4, parallax: 1.0, apparentScale: 1.01 },
  "right-top": { z: -90, rotateY: -3, parallax: 0.35, apparentScale: 0.985 },
  "centre-top": { z: 0, rotateY: 0, parallax: 0.55, apparentScale: 1 },
  "left-bottom": { z: 90, rotateY: 4, parallax: 1.1, apparentScale: 1.01 },
  "centre-bottom": { z: -30, rotateY: 0, parallax: 0.5, apparentScale: 0.99 },
  "right-bottom": { z: 80, rotateY: -4, parallax: 1.0, apparentScale: 1.01 },
};

/**
 * The `scale()` a plane at depth `z` needs so it APPEARS `apparentScale`
 * times its flat size under the deck's perspective. Without it a plane
 * 90 px in front of the wall would render 7 % larger and run into its
 * neighbour across a 12 px gap.
 */
export function compensatingScale(z: number, apparentScale = 1): number {
  const projected = DECK_PERSPECTIVE_PX / (DECK_PERSPECTIVE_PX - z);
  return round(apparentScale / projected, 4);
}

/**
 * The CSS custom properties one slot's wrapper carries — everything the
 * stylesheet's single `transform` rule reads.
 */
export function slotDepthVars(slot: BoardSlot): Record<`--${string}`, string> {
  const d = SLOT_DEPTH[slot];
  return {
    "--slot-z": `${d.z}px`,
    "--slot-ry": `${d.rotateY}deg`,
    "--slot-par": `${d.parallax}`,
    "--slot-scale": `${compensatingScale(d.z, d.apparentScale)}`,
  };
}

/**
 * The pre-translation a plane needs so that, at depth `z`, its centre still
 * PROJECTS onto the place the grid gave it. Under one shared perspective a
 * point at `(cx, cy)` from the vanishing point projects to `c · p/(p−z)` — a
 * near plane at the board's edge drifts outward and is clipped by the board's
 * box (seen 2026-08-22: the log's left edge and the outputs card cut off). A
 * shift of `−c·z/p` beforehand cancels exactly that drift, so the depth
 * shows in scale, turn, shadow and parallax, never in a card leaving its slot.
 */
export function driftCompensation(
  cx: number,
  cy: number,
  z: number,
): { dx: number; dy: number } {
  const k = z / DECK_PERSPECTIVE_PX;
  return { dx: round(-cx * k, 2) || 0, dy: round(-cy * k, 2) || 0 };
}

/**
 * Where the board's vanishing point sits in its own box — the same place the
 * stylesheet's `perspective-origin` names (50 % across, 42 % down: a little
 * above the middle, where the mascot's eyes are).
 */
export const PERSPECTIVE_ORIGIN = { x: 0.5, y: 0.42 };

/**
 * The pointer's place on the board as −1…1 in each axis, from the event's
 * client coordinates and the board's box — 0,0 at the centre, clamped at the
 * edges. Pure, so the listener only has to call it.
 */
export function pointerOffset(
  clientX: number,
  clientY: number,
  box: { left: number; top: number; width: number; height: number },
): { x: number; y: number } {
  if (box.width <= 0 || box.height <= 0) return { x: 0, y: 0 };
  const x = ((clientX - box.left) / box.width) * 2 - 1;
  const y = ((clientY - box.top) / box.height) * 2 - 1;
  return { x: clamp(x, -1, 1), y: clamp(y, -1, 1) };
}

/**
 * The parallax shift of the NEAREST plane for a pointer offset — planes
 * move AGAINST the pointer (the case sways away from where you look, as a
 * real display case would when you lean), and only a little: the distances
 * have to read, the board must not wobble.
 */
export function parallaxShift(offset: { x: number; y: number }): { x: number; y: number } {
  // `|| 0` folds −0 into 0 so a rest position never prints as "-0px".
  return {
    x: round(-offset.x * PARALLAX_MAX_PX, 2) || 0,
    y: round(-offset.y * PARALLAX_MAX_PX * 0.6, 2) || 0,
  };
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

function round(v: number, places: number): number {
  const f = 10 ** places;
  return Math.round(v * f) / f;
}
