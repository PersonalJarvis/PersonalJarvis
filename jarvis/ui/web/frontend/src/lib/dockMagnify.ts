/**
 * The dock's magnification curve — the pure math behind DeckDock.tsx.
 *
 * The effect is a hill of SIZES, not of positions: the icon under the pointer
 * grows, its neighbours grow a little less, everything else stays at rest —
 * and no icon ever leaves its place. Each one grows around its own rest
 * centre. That is a deliberate departure from the desktop docks, which push
 * neighbours apart to make room: the maintainer wants the column rigid, so
 * that a hover reads as "this one lights up" and never as "the row shuffles".
 * Boxes may therefore overlap by a few px at the peak; the dock draws only the
 * hovered box with a surface, on top, so the overlap is never visible.
 *
 * Two things make the hill feel right rather than jumpy, and both live here so
 * they can be tested without a DOM:
 *
 * 1. A cosine falloff over a fixed radius. Linear falloff has a visible kink
 *    where it hits zero; cosine eases into rest.
 * 2. Scales measured against the REST centres. Positions never move, so the
 *    icon under the pointer in rest space is also the one visibly under it —
 *    a single hit test (`dockSlotAt`) serves both.
 */

/** Largest scale for the icon directly under the pointer. */
export const DOCK_MAX_SCALE = 1.5;
/** How far (in base-icon units) the hill reaches to either side. */
export const DOCK_RADIUS_UNITS = 2.4;

/**
 * Scale for one icon given the pointer's distance from its rest centre, in
 * base-icon units. 1 at rest, `DOCK_MAX_SCALE` when the pointer is centred.
 */
export function magnifyScale(
  distanceUnits: number,
  maxScale = DOCK_MAX_SCALE,
  radiusUnits = DOCK_RADIUS_UNITS,
): number {
  const d = Math.abs(distanceUnits);
  if (!Number.isFinite(d) || d >= radiusUnits) return 1;
  // Cosine window: 1 at d=0, 0 at d=radius, zero slope at both ends.
  const w = 0.5 * (1 + Math.cos((Math.PI * d) / radiusUnits));
  return 1 + (maxScale - 1) * w;
}

export interface DockLayoutItem {
  scale: number;
  /** Centre along the dock axis, in px from the dock's start (rest gap included). Never moves. */
  center: number;
  /** Rendered size along the axis, in px — grows around `center`. */
  size: number;
}

export interface DockLayout {
  items: DockLayoutItem[];
  /** Where the row of REST boxes ends, in px from the dock's start (trailing gap included). Constant. */
  extent: number;
}

/**
 * Lay out `count` icons of `baseSize` px with `gap` px between them, given the
 * pointer position along the axis (`pointer`, px from the start; null = at
 * rest). Returns one entry per icon and the (constant) rest extent.
 *
 * Centres are the rest centres whatever the pointer does; only `size` and
 * `scale` respond to it.
 */
export function layoutDock(
  count: number,
  baseSize: number,
  gap: number,
  pointer: number | null,
  maxScale = DOCK_MAX_SCALE,
  radiusUnits = DOCK_RADIUS_UNITS,
): DockLayout {
  const stride = baseSize + gap;
  const items: DockLayoutItem[] = [];
  for (let i = 0; i < count; i++) {
    const center = gap + i * stride + baseSize / 2;
    const scale =
      pointer === null ? 1 : magnifyScale((pointer - center) / stride, maxScale, radiusUnits);
    items.push({ scale, center, size: baseSize * scale });
  }
  return { items, extent: gap + count * stride };
}

/**
 * Which icon's slot a rest-space position falls into: the icon's box plus half
 * a gap on either side, so there is no dead zone between neighbours. -1 when
 * the position is outside the row.
 */
export function dockSlotAt(pointer: number, count: number, baseSize: number, gap: number): number {
  if (!Number.isFinite(pointer) || count <= 0) return -1;
  const i = Math.floor((pointer - gap / 2) / (baseSize + gap));
  return i >= 0 && i < count ? i : -1;
}
