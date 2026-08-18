/**
 * The dock's magnification curve — the pure math behind DeckDock.tsx.
 *
 * The effect is the one desktop docks made familiar: the icon under the
 * pointer grows, its neighbours grow a little less, and everything else stays
 * put — a smooth hill, not a step. Three things make it feel right rather than
 * jumpy, and all three live here so they can be tested without a DOM:
 *
 * 1. A cosine falloff over a fixed radius. Linear falloff has a visible kink
 *    where it hits zero; cosine eases into rest.
 * 2. Positions derived from the SCALES, not from the pointer. Each icon's
 *    centre is the running sum of the sizes before it, so growing one pushes
 *    the rest outward by exactly the space it takes — no overlap, no gap.
 * 3. The row is ANCHORED AT THE POINTER. Summing sizes from the top pushes
 *    every magnified icon downward, away from the pointer that is magnifying
 *    it — by almost a full icon in the middle of a long dock, which is what
 *    made the old dock feel as if the hill lagged behind the mouse. The real
 *    desktop docks lift the row so the point under the pointer stays exactly
 *    where it is: what grows above the pointer moves up, what grows below
 *    moves down. `headroom` is how far the row may be lifted (the space the
 *    dock reserves above its first icon at rest); `maxAnchorLift` says how
 *    much a given geometry needs.
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
  /** Centre along the dock axis, in px from the dock's start (rest gap included). */
  center: number;
  /** Rendered size along the axis, in px. */
  size: number;
}

export interface DockLayout {
  items: DockLayoutItem[];
  /** Where the row ends, in px from the dock's start (trailing gap included). */
  extent: number;
  /**
   * How far the whole row was lifted to keep the point under the pointer in
   * place: 0 at rest, negative while magnified, never below `-headroom`.
   */
  shift: number;
}

/**
 * Lay out `count` icons of `baseSize` px with `gap` px between them, given the
 * pointer position along the axis (`pointer`, px from the start; null = at
 * rest). Returns one entry per icon and the total extent.
 *
 * Positions come from the scales — every icon starts where the previous one
 * ended — so the row grows to make room instead of overlapping. With a
 * `headroom` > 0 the row is then lifted (by at most that much) so the point of
 * the rest layout under the pointer stays under the pointer.
 */
export function layoutDock(
  count: number,
  baseSize: number,
  gap: number,
  pointer: number | null,
  maxScale = DOCK_MAX_SCALE,
  radiusUnits = DOCK_RADIUS_UNITS,
  headroom = 0,
): DockLayout {
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

  let shift = 0;
  if (pointer !== null && headroom > 0 && count > 0) {
    // The "material point": which slot of the REST layout is under the pointer,
    // and how far into it. A pointer past either end pins that end's edge, so
    // an icon approached from beyond the row grows towards the pointer.
    const rowStart = gap;
    const rowEnd = gap + (count - 1) * stride + baseSize;
    const p = Math.min(rowEnd, Math.max(rowStart, pointer));
    const i = Math.min(count - 1, Math.max(0, Math.floor((p - rowStart) / stride)));
    const within = p - (rowStart + i * stride);
    const it = items[i];
    const magnified =
      within <= baseSize
        ? it.center - it.size / 2 + (within / baseSize) * it.size
        : it.center + it.size / 2 + (within - baseSize); // in the gap below: gaps do not grow
    // Growth only ever pushes material down, so the lift is never positive.
    shift = Math.max(-headroom, Math.min(0, p - magnified));
    if (shift !== 0) {
      for (const item of items) item.center += shift;
    }
  }
  return { items, extent: cursor + shift, shift };
}

/**
 * Which icon's slot a rest-space position falls into: the icon's box plus half
 * a gap on either side, so there is no dead zone between neighbours. -1 when
 * the position is outside the row.
 *
 * Because the layout is anchored at the pointer, the slot under the pointer in
 * REST space is also the icon visibly under it — one hit test serves both.
 */
export function dockSlotAt(pointer: number, count: number, baseSize: number, gap: number): number {
  if (!Number.isFinite(pointer) || count <= 0) return -1;
  const i = Math.floor((pointer - gap / 2) / (baseSize + gap));
  return i >= 0 && i < count ? i : -1;
}

/**
 * The largest lift `layoutDock` will ever ask for with this geometry — the
 * headroom a dock has to reserve above its first icon so the anchoring is
 * never clipped. Found by sampling pointer positions across one stride in
 * the middle of a long row (that is where the hill has the most material
 * above the pointer). Rounded up to whole px.
 */
export function maxAnchorLift(
  baseSize: number,
  gap: number,
  maxScale = DOCK_MAX_SCALE,
  radiusUnits = DOCK_RADIUS_UNITS,
): number {
  const stride = baseSize + gap;
  const count = Math.ceil(radiusUnits) * 2 + 3;
  const mid = Math.floor(count / 2);
  const start = gap + mid * stride;
  let worst = 0;
  const steps = 48;
  for (let s = 0; s <= steps; s++) {
    const p = start + (stride * s) / steps;
    // A generous headroom so the sample measures the unclamped lift.
    const { shift } = layoutDock(count, baseSize, gap, p, maxScale, radiusUnits, baseSize * count);
    worst = Math.max(worst, -shift);
  }
  return Math.ceil(worst);
}
