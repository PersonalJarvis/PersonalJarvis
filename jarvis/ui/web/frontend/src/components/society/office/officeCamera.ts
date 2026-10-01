/**
 * The office camera: a three-quarter view from the south-east, looking down
 * at about 40°, framed so the whole floor fits the viewport. Pure and tested.
 */
export const CAMERA_FOV = 35;

export const CAMERA_LIMITS = {
  /** Polar angle from straight down: ~20° (near top-down) to ~72° (near eye level). */
  minPolar: 0.35,
  maxPolar: 1.25,
  /** Close enough to read a desk or a wardrobe shelf, far enough to stay outside the character. */
  minDistance: 2.5,
  maxDistance: 140,
} as const;

/**
 * A press that travelled further than this (CSS px) turned the camera; the
 * click it ends in is not a click. Measured over the whole press, so a drag
 * that comes back to where it started still counts as a drag.
 */
export const DRAG_CLICK_PX = 6;

/** One mouse-wheel notch, in CSS px, as browsers report it in pixel mode. */
const WHEEL_NOTCH_PX = 100;
/** How far one notch zooms, as OrbitControls' zoomSpeed: 0.95^2.4 ≈ 12 % closer or further. */
const ZOOM_SPEED_PER_NOTCH = 2.4;
/**
 * OrbitControls' zoomSpeed for one wheel event, proportional to how far the
 * wheel turned. The controls zoom a fixed step per event whatever its size:
 * a mouse notch crawled (~20 notches from the overview to the character) and
 * a trackpad, which sends dozens of tiny events, raced. `deltaMode` 1 is
 * lines, 2 pages (Firefox, some mice).
 */
export function wheelZoomSpeed(deltaY: number, deltaMode = 0): number {
  const px = Math.abs(deltaY) * (deltaMode === 1 ? 40 : deltaMode === 2 ? 800 : 1);
  return Math.min(ZOOM_SPEED_PER_NOTCH * 2.5, Math.max(0.02, (px / WHEEL_NOTCH_PX) * ZOOM_SPEED_PER_NOTCH));
}

/** Look-down angle below the horizon and yaw east of south for the home view. */
export const HOME_PITCH_RAD = (40 * Math.PI) / 180;
export const HOME_YAW_RAD = (38 * Math.PI) / 180;

export interface Bounds { minX: number; maxX: number; minZ: number; maxZ: number }
export interface CameraPose { position: [number, number, number]; target: [number, number, number] }

/** Distance that fits a floor of this footprint into a viewport of this aspect. */
export function fitDistance(bounds: Bounds, aspect: number, fovDeg = CAMERA_FOV, tightness = 0.62): number {
  const width = bounds.maxX - bounds.minX;
  const depth = bounds.maxZ - bounds.minZ;
  // Seen diagonally, the floor's screen width is roughly its diagonal; its
  // screen height is the diagonal foreshortened by the look-down angle.
  const diagonal = Math.hypot(width, depth);
  const vHalf = ((fovDeg * Math.PI) / 180) / 2;
  const hHalf = Math.atan(Math.tan(vHalf) * Math.max(0.3, aspect));
  const needH = (diagonal * 0.5) / Math.tan(hHalf);
  const needV = (diagonal * Math.sin(HOME_PITCH_RAD) * 0.5 + 1.5) / Math.tan(vHalf);
  return Math.min(CAMERA_LIMITS.maxDistance, Math.max(CAMERA_LIMITS.minDistance, Math.max(needH, needV) * tightness));
}

/** Smallest area the home view frames, so a lone agent still shows its neighbourhood. */
const MIN_FOCUS_W = 18;
const MIN_FOCUS_D = 14;

/** Pad the occupied area to a readable neighbourhood around it. */
export function focusBounds(points: readonly { x: number; z: number }[]): Bounds | null {
  if (points.length === 0) return null;
  const xs = points.map((p) => p.x), zs = points.map((p) => p.z);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cz = (Math.min(...zs) + Math.max(...zs)) / 2;
  const w = Math.max(MIN_FOCUS_W, Math.max(...xs) - Math.min(...xs) + 6);
  const d = Math.max(MIN_FOCUS_D, Math.max(...zs) - Math.min(...zs) + 6);
  return { minX: cx - w / 2, maxX: cx + w / 2, minZ: cz - d / 2, maxZ: cz + d / 2 };
}

/** Home view: frame where the agents sit, or the whole floor when nobody does. */
export function cameraHome(bounds: Bounds, aspect: number, focus: Bounds | null = null): CameraPose {
  const frame = focus ?? bounds;
  const target: [number, number, number] = [(frame.minX + frame.maxX) / 2, 0, (frame.minZ + frame.maxZ) / 2];
  // The whole floor must fit edge to edge; a focus area may crop its padding.
  const distance = fitDistance(frame, aspect, CAMERA_FOV, focus ? 0.62 : 0.9);
  const horizontal = Math.cos(HOME_PITCH_RAD) * distance;
  return {
    target,
    position: [
      target[0] + Math.sin(HOME_YAW_RAD) * horizontal,
      Math.sin(HOME_PITCH_RAD) * distance,
      target[2] + Math.cos(HOME_YAW_RAD) * horizontal,
    ],
  };
}
