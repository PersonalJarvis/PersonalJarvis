/**
 * Geometry of the coding-session window on the office map: where it opens,
 * how it moves and resizes, and how it stays inside the stage. Pure, apart
 * from the two storage helpers at the bottom.
 *
 * The window is placed in stage pixels (top-left origin) but remembered as a
 * distance from the stage's bottom-right corner, where it opens: a stage that
 * grows or shrinks (the IDE's side panel dragged wider) keeps the window where
 * the reader put it relative to that corner instead of stranding it mid-map.
 */

export interface WindowRect { x: number; y: number; w: number; h: number }
export interface StageSize { width: number; height: number }
/** The edge or corner a resize grips, by compass point. */
export type ResizeEdge = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
/** How the window is remembered: its size and its distance from the stage's bottom-right corner. */
interface StoredWindow { r: number; b: number; w: number; h: number }

/** Space kept between the window and every stage edge. */
export const EDGE = 8;
/** Smallest window: a title bar and a terminal a coding CLI can still draw a prompt in. */
export const MIN_W = 360;
export const MIN_H = 220;

/** Room left under the window, per stage: the compact stage's HUD buttons sit along its bottom. */
function bottomGap(compact: boolean): number {
  return compact ? 48 : 16;
}

const clamp = (value: number, lo: number, hi: number) => Math.min(Math.max(value, lo), hi);

/** Where the window opens: bottom-right, about half the stage wide, the old panel's height. */
export function defaultRect(stage: StageSize, compact: boolean): WindowRect {
  const right = compact ? EDGE : 14;
  const bottom = bottomGap(compact);
  const w = Math.round(clamp(stage.width * 0.48, 460, 860));
  const h = Math.round(clamp(stage.height * 0.42, 280, 480));
  return fitRect({ x: stage.width - right - w, y: stage.height - bottom - h, w, h }, stage);
}

/** The rect made to fit the stage: shrunk if it is too big, then moved fully inside. */
export function fitRect(rect: WindowRect, stage: StageSize): WindowRect {
  const maxW = Math.max(0, stage.width - 2 * EDGE);
  const maxH = Math.max(0, stage.height - 2 * EDGE);
  const w = Math.round(clamp(rect.w, Math.min(MIN_W, maxW), maxW));
  const h = Math.round(clamp(rect.h, Math.min(MIN_H, maxH), maxH));
  return {
    x: Math.round(clamp(rect.x, EDGE, Math.max(EDGE, stage.width - EDGE - w))),
    y: Math.round(clamp(rect.y, EDGE, Math.max(EDGE, stage.height - EDGE - h))),
    w,
    h,
  };
}

/** The window carried by its title bar `dx`/`dy` pixels from where the drag began. */
export function moveRect(start: WindowRect, dx: number, dy: number, stage: StageSize): WindowRect {
  return fitRect({ ...start, x: start.x + dx, y: start.y + dy }, stage);
}

/**
 * The window resized by one edge or corner. The opposite edge stays where it
 * was, and the gripped one stops at the minimum size and at the stage edge.
 */
export function resizeRect(start: WindowRect, edge: ResizeEdge, dx: number, dy: number, stage: StageSize): WindowRect {
  let { x, y, w, h } = start;
  const minW = Math.min(MIN_W, stage.width - 2 * EDGE);
  const minH = Math.min(MIN_H, stage.height - 2 * EDGE);
  if (edge.includes("e")) w = clamp(start.w + dx, minW, stage.width - EDGE - start.x);
  if (edge.includes("w")) {
    const right = start.x + start.w;
    w = clamp(start.w - dx, minW, right - EDGE);
    x = right - w;
  }
  if (edge.includes("s")) h = clamp(start.h + dy, minH, stage.height - EDGE - start.y);
  if (edge.includes("n")) {
    const bottom = start.y + start.h;
    h = clamp(start.h - dy, minH, bottom - EDGE);
    y = bottom - h;
  }
  return fitRect({ x, y, w, h }, stage);
}

/** The remembered window placed on a stage of this size. */
export function placeStored(stored: StoredWindow, stage: StageSize): WindowRect {
  return fitRect({ x: stage.width - stored.r - stored.w, y: stage.height - stored.b - stored.h, w: stored.w, h: stored.h }, stage);
}

/** What to remember of a window on a stage of this size. */
export function toStored(rect: WindowRect, stage: StageSize): StoredWindow {
  return { r: stage.width - rect.x - rect.w, b: stage.height - rect.y - rect.h, w: rect.w, h: rect.h };
}

/** One place per stage kind: the IDE's side panel and the full office page differ a lot in size. */
const storageKey = (compact: boolean) => `jarvis.office.paneWindow.${compact ? "compact" : "full"}`;

/** The window the reader last left, or null (never moved, storage blocked, or garbage). */
export function loadStoredWindow(compact: boolean): StoredWindow | null {
  try {
    const raw = window.localStorage.getItem(storageKey(compact));
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<StoredWindow>;
    const numbers = [value.r, value.b, value.w, value.h];
    if (!numbers.every((n) => typeof n === "number" && Number.isFinite(n))) return null;
    return value as StoredWindow;
  } catch {
    // Storage can be blocked outright (private windows, site data off): the
    // window simply opens in its default place.
    return null;
  }
}

/** Remember the window, or forget it (null) so it opens in its default place again. */
export function saveStoredWindow(compact: boolean, stored: StoredWindow | null): void {
  try {
    if (stored) window.localStorage.setItem(storageKey(compact), JSON.stringify(stored));
    else window.localStorage.removeItem(storageKey(compact));
  } catch {
    // Blocked storage only costs the remembered place; the window still works.
  }
}
