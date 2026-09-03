/**
 * The bird's-eye camera as numbers: pitch, yaw, the three zoom steps and the
 * conversions between screen pixels and ground metres. Pure, pinned by
 * `worldCamera.test.ts`; the R3F rig only reads these.
 *
 * Decisions (maintainer, 2026-09-01): a STEEP bird's-eye view — 50° below
 * the horizon at the classic 45° dimetric yaw — so the island reads as "seen
 * from above" while house fronts and figure silhouettes stay visible. Zoom
 * is five fixed steps (one field, four fields, the market district, a quarter
 * of the island, the whole island), never continuous: with an integer pixel
 * size every step stays crisp, and the widest step is the postcard view.
 *
 * Decision (maintainer, 2026-09-03): those two angles are the STARTING view,
 * not the only one. The viewer orbits the island — yaw all the way round, so
 * every building can be looked at from behind, and pitch between a low
 * street-level slant and a near-vertical map view. Both stay free (no snapping
 * mid-drag); the keyboard and the HUD compass step yaw in quarter turns so
 * getting back to the designed view is one click.
 */
import { ISLAND_HALF_M } from "./islandLayout";

export const CAMERA_PITCH_DEG = 50;
export const CAMERA_YAW_DEG = 45;
/**
 * Far enough that nothing on the island reaches behind the camera. An
 * orthographic camera draws the same picture from any distance, so this only
 * has to clear the clipping planes — and the flattest allowed view is what
 * decides it: looking along the ground from 20°, the far side of the island
 * lies 2 · ISLAND_HALF_M · cos(20°) ≈ 481 m further along the view axis than
 * the near side, and the target may sit at either end. `CAMERA_FAR_M` then
 * has to hold the whole of that span on the other side.
 */
export const CAMERA_DISTANCE_M = 640;
export const CAMERA_FAR_M = 1500;

/**
 * How far the view may be tilted. Flatter than `MIN` and the island hides
 * behind its own hills while a pointer drag pans absurdly far (the pan
 * stretches by 1 / sin(pitch)); steeper than `MAX` and the houses lose their
 * fronts and the world reads as a flat map.
 */
export const MIN_PITCH_DEG = 20;
export const MAX_PITCH_DEG = 80;

/** Yaw the compass and the keyboard step in — a quarter turn per press. */
export const YAW_STEP_DEG = 45;

/**
 * Orbit speed: dragging across the full stage width turns a whole revolution,
 * and across its full height sweeps the tilt twice from end to end — so half
 * a stage of travel covers the whole tilt range, and a short drag still
 * changes it noticeably. Relative to the stage, so a small pane turns as fast
 * as a large one.
 */
export const ORBIT_YAW_PER_WIDTH_DEG = 360;
export const ORBIT_PITCH_PER_HEIGHT_DEG = 120;

/** Ground width the orthographic frustum shows per zoom step, in metres. */
export const ZOOM_WIDTHS_M = [32, 64, 128, 256, 512] as const;
export type ZoomLevel = 0 | 1 | 2 | 3 | 4;
/** The widest step: the whole island in one frame. */
export const MAX_ZOOM: ZoomLevel = 4;
/** Start on the middle step: the whole village square fits, figures stay readable. */
export const DEFAULT_ZOOM: ZoomLevel = 1;

/**
 * Screen pixels per rendered pixel — the "fine" grain the maintainer chose
 * (a 1280-px-wide stage renders at 640 px). 3 would be "light", 4 "coarse".
 */
export const PIXEL_SIZE = 2;

/** Keyboard pan speed as a fraction of the visible width per second. */
export const KEY_PAN_PER_S = 0.9;

const DEG = Math.PI / 180;

/** Camera position relative to its target for the given pitch/yaw/distance. */
export function cameraOffset(
  pitchDeg = CAMERA_PITCH_DEG,
  yawDeg = CAMERA_YAW_DEG,
  distance = CAMERA_DISTANCE_M,
): [number, number, number] {
  const p = pitchDeg * DEG;
  const y = yawDeg * DEG;
  return [
    round(distance * Math.cos(p) * Math.sin(y)),
    round(distance * Math.sin(p)),
    round(distance * Math.cos(p) * Math.cos(y)),
  ];
}

/** Half extents of the orthographic frustum for a visible ground width. */
export function orthoHalfExtents(widthM: number, aspect: number): { halfW: number; halfH: number } {
  const halfW = widthM / 2;
  return { halfW, halfH: halfW / Math.max(aspect, 1e-6) };
}

/**
 * The two ground directions the screen axes map to: `right` is screen-right,
 * `forward` is screen-up projected onto the ground (away from the camera).
 * Both are unit vectors in the xz plane, given as [x, z].
 */
export function groundBasis(yawDeg = CAMERA_YAW_DEG): { right: [number, number]; forward: [number, number] } {
  const y = yawDeg * DEG;
  return {
    right: [round(Math.cos(y)), round(-Math.sin(y))],
    forward: [round(-Math.sin(y)), round(-Math.cos(y))],
  };
}

/**
 * How far the camera target moves for a pointer drag of (dx, dy) pixels —
 * the world follows the pointer, so the target moves the other way. Vertical
 * pixels cover more ground than horizontal ones because the camera looks down
 * at `pitch`: one screen metre up is 1 / sin(pitch) metres of ground.
 */
export function dragToPan(
  dxPx: number,
  dyPx: number,
  widthM: number,
  stageWidthPx: number,
  pitchDeg = CAMERA_PITCH_DEG,
  yawDeg = CAMERA_YAW_DEG,
): [number, number] {
  if (stageWidthPx <= 0) return [0, 0];
  const mpp = widthM / stageWidthPx;
  const { right, forward } = groundBasis(yawDeg);
  const along = -dxPx * mpp;
  const ahead = (dyPx * mpp) / Math.sin(pitchDeg * DEG);
  return [round(along * right[0] + ahead * forward[0]), round(along * right[1] + ahead * forward[1])];
}

/** Keep the target on the island (with a small margin of sea). */
export function clampTarget(x: number, z: number, half = ISLAND_HALF_M): [number, number] {
  return [Math.min(half, Math.max(-half, x)), Math.min(half, Math.max(-half, z))];
}

export function stepZoom(level: ZoomLevel, direction: 1 | -1): ZoomLevel {
  const next = Math.min(ZOOM_WIDTHS_M.length - 1, Math.max(0, level + direction));
  return next as ZoomLevel;
}

/** Wrap a yaw into [0, 360) — the store never holds a wound-up angle. */
export function normalizeYaw(deg: number): number {
  if (!Number.isFinite(deg)) return CAMERA_YAW_DEG;
  return round(((deg % 360) + 360) % 360);
}

/** Keep the tilt inside the range the island still reads in. */
export function clampPitch(deg: number): number {
  if (!Number.isFinite(deg)) return CAMERA_PITCH_DEG;
  return round(Math.min(MAX_PITCH_DEG, Math.max(MIN_PITCH_DEG, deg)));
}

/**
 * How much a pointer drag of (dx, dy) pixels turns the view, as
 * [yaw, pitch] degrees. The island follows the pointer, the same grab
 * metaphor the pan uses: drag right and the island spins right, drag down and
 * it tips forward so more of its roofs come into view.
 */
export function dragToOrbit(
  dxPx: number,
  dyPx: number,
  stageWidthPx: number,
  stageHeightPx: number,
): [number, number] {
  const dYaw = stageWidthPx > 0 ? (dxPx / stageWidthPx) * ORBIT_YAW_PER_WIDTH_DEG : 0;
  const dPitch = stageHeightPx > 0 ? (dyPx / stageHeightPx) * ORBIT_PITCH_PER_HEIGHT_DEG : 0;
  return [round(dYaw), round(dPitch)];
}

/**
 * The next quarter-turn stop from `yawDeg` in `direction`. Already on a stop
 * (within a degree) it moves a full step; off one it lands on the next stop,
 * so a stepped turn always ends on the grid the island was designed for.
 */
export function stepYaw(yawDeg: number, direction: 1 | -1): number {
  const cur = normalizeYaw(yawDeg) / YAW_STEP_DEG;
  // Half a degree of slack: an angle that close to a stop counts as being on
  // it, so a press there moves on instead of snapping back to where it is.
  const eps = 0.5 / YAW_STEP_DEG;
  const next = direction > 0 ? Math.floor(cur + eps) + 1 : Math.ceil(cur - eps) - 1;
  return normalizeYaw(next * YAW_STEP_DEG);
}

/**
 * Whether the view still stands where the island was designed to be seen
 * from. The compass marks itself while it does not, so a viewer who turned
 * the island by accident can see why the fronts are facing away.
 */
export function isDesignedView(yawDeg: number, pitchDeg: number): boolean {
  return Math.abs(shortestYawDelta(yawDeg, CAMERA_YAW_DEG)) < 0.5 && Math.abs(pitchDeg - CAMERA_PITCH_DEG) < 0.5;
}

/**
 * Where north points on screen, as an SVG rotation in degrees: the ground
 * vector (0, −1) read through `groundBasis` lands at (sin yaw, cos yaw) on
 * screen, which is a clockwise rotation by the yaw itself.
 */
export function northScreenAngle(yawDeg: number): number {
  return normalizeYaw(yawDeg);
}

/** Signed shortest way from `fromDeg` to `toDeg`, in (−180, 180]. */
export function shortestYawDelta(fromDeg: number, toDeg: number): number {
  let d = (toDeg - fromDeg) % 360;
  if (d > 180) d -= 360;
  if (d <= -180) d += 360;
  return round(d);
}

/**
 * The four ground corners the viewport covers around `target`, in screen
 * order (top-left, top-right, bottom-right, bottom-left). Used by the minimap
 * to draw the viewport as a rotated rectangle.
 */
export function visibleGroundCorners(
  target: [number, number],
  widthM: number,
  aspect: number,
  pitchDeg = CAMERA_PITCH_DEG,
  yawDeg = CAMERA_YAW_DEG,
): Array<[number, number]> {
  const { right, forward } = groundBasis(yawDeg);
  const { halfW, halfH } = orthoHalfExtents(widthM, aspect);
  const depth = halfH / Math.sin(pitchDeg * DEG);
  const corner = (sx: number, sz: number): [number, number] => [
    round(target[0] + sx * halfW * right[0] + sz * depth * forward[0]),
    round(target[1] + sx * halfW * right[1] + sz * depth * forward[1]),
  ];
  return [corner(-1, 1), corner(1, 1), corner(1, -1), corner(-1, -1)];
}

/** Exponential follow factor for one frame of `dt` seconds at `rate` per second. */
export function followAlpha(dt: number, rate: number): number {
  return 1 - Math.exp(-rate * Math.max(0, dt));
}

function round(v: number): number {
  return Math.round(v * 1e6) / 1e6;
}
