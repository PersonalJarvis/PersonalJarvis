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
 */
import { ISLAND_HALF_M } from "./islandLayout";

export const CAMERA_PITCH_DEG = 50;
export const CAMERA_YAW_DEG = 45;
/** Far enough that nothing on the island reaches behind the camera. */
export const CAMERA_DISTANCE_M = 420;

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
