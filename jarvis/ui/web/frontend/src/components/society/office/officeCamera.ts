/**
 * The office camera: a three-quarter view from the south-east, looking down
 * at about 40°, framed so the whole floor fits the viewport. Pure and tested.
 */
export const CAMERA_FOV = 35;

export const CAMERA_LIMITS = {
  /** Polar angle from straight down: ~20° (near top-down) to ~72° (near eye level). */
  minPolar: 0.35,
  maxPolar: 1.25,
  minDistance: 4,
  maxDistance: 140,
} as const;

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
