/**
 * The dock's magnification curve — the pure math behind DeckDock.tsx.
 *
 * The effect the maintainer asked for is the one desktop docks made familiar:
 * the icon under the pointer grows, its neighbours grow a little less, and
 * everything else stays put — a smooth hill, not a step. Two things make it
 * feel right rather than jumpy, and both live here so they can be tested
 * without a DOM:
 *
 * 1. A cosine falloff over a fixed radius. Linear falloff has a visible kink
 *    where it hits zero; cosine eases into rest.
 * 2. Positions derived from the SCALES, not from the pointer. Each icon's
 *    centre is the running sum of the sizes before it, so growing one pushes
 *    the rest outward by exactly the space it takes — no overlap, no gap.
 */

/** Largest scale for the icon directly under the pointer. */
export const DOCK_MAX_SCALE = 1.75;
/** How far (in base-icon units) the hill reaches to either side. */
export const DOCK_RADIUS_UNITS = 2.6;

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
  /** Centre along the dock axis, in px from the dock's start (rest gap included). */
  center: number;
  /** Rendered size along the axis, in px. */
  size: number;
}

/**
 * Lay out `count` icons of `baseSize` px with `gap` px between them, given the
 * pointer position along the axis (`pointer`, px from the start; null = at
 * rest). Returns one entry per icon and the total extent.
 *
 * Positions come from the scales — every icon starts where the previous one
 * ended — so the row grows to make room instead of overlapping.
 */
export function layoutDock(
  count: number,
  baseSize: number,
  gap: number,
  pointer: number | null,
  maxScale = DOCK_MAX_SCALE,
  radiusUnits = DOCK_RADIUS_UNITS,
): { items: DockLayoutItem[]; extent: number } {
  const items: DockLayoutItem[] = [];
  const stride = baseSize + gap;
  // Rest centres are what the pointer is measured against: measuring against
  // the *magnified* centres would make an icon chase the pointer as it grows.
  const restCenters = Array.from({ length: count }, (_, i) => gap + i * stride + baseSize / 2);

  let cursor = gap;
  for (let i = 0; i < count; i++) {
    const scale =
      pointer === null
        ? 1
        : magnifyScale((pointer - restCenters[i]) / stride, maxScale, radiusUnits);
    const size = baseSize * scale;
    items.push({ scale, center: cursor + size / 2, size });
    cursor += size + gap;
  }
  return { items, extent: cursor };
}
