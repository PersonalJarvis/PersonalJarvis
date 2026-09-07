/**
 * The mission deck as a ROOM — the numbers behind `components/deck/room/`.
 *
 * The room is a display case in real 3D (three.js): the wallpaper is the
 * back wall, a reflecting floor runs from it toward the viewer, the mascot
 * stands on that floor, and the instruments stand around it as upright
 * glass panels — near the viewer what the person acts on (log, outputs,
 * terminals), on the wall what is looked at (response, memory map,
 * capture). The camera has a fixed standpoint and sways a little with the
 * pointer; it never walks.
 *
 * Everything here is pure so `deckRoom.test.ts` can pin it: world units
 * (1 unit ≈ 1 m in a room about 16 m wide), panel placement in those units,
 * the DOM size each panel renders at, the camera's rest pose and sway, and
 * the wall's cover-fit. The components only read these.
 */

/** The camera's rest pose: standing in front of the case, a little above the floor's middle. */
export const CAMERA = {
  fov: 40,
  position: [0, 1.2, 14] as const,
  lookAt: [0, 0.3, 0] as const,
  /** How far (world units) the pointer moves the camera, edge to edge. */
  swayX: 0.7,
  swayY: 0.35,
  /** How fast the camera follows the pointer (per second, for a lerp). */
  follow: 4,
};

/** The floor's height: the bottom edge of the wall, where everything stands. */
export const FLOOR_Y = -2.6;

/** The mascot's height in world units and where it stands. */
export const MASCOT = { height: 3.1, position: [0, FLOOR_Y, 3.4] as const };

/** One CSS3D pixel is this many world units: a 420 px panel is 4.2 units wide. */
export const PANEL_SCALE = 0.01;

export type RoomPanelId = "response" | "log" | "activity" | "wiki" | "terminals" | "capture" | "headline";

export interface RoomPanel {
  id: RoomPanelId;
  /** Centre of the panel in world units. */
  position: [number, number, number];
  /** Turn about the vertical axis, in radians (left panels +, right −). */
  rotationY: number;
  /** The DOM box the panel renders into, in px (world size = px × PANEL_SCALE). */
  width: number;
  height: number;
  /** Panels on the wall are lit from the front; standing panels cast a shadow. */
  standing: boolean;
}

/**
 * Where the instruments stand. The near panels frame the mascot on both
 * sides like stands in an exhibition, turned toward the viewer; the wall
 * panels sit just in front of the picture. Heights keep every panel's
 * bottom edge on or above the floor.
 */
export const ROOM_PANELS: readonly RoomPanel[] = [
  // on the wall
  { id: "response", position: [0, 2.5, 0.35], rotationY: 0, width: 760, height: 250, standing: false },
  { id: "wiki", position: [6.1, 1.45, 0.55], rotationY: -0.28, width: 520, height: 440, standing: false },
  { id: "capture", position: [0, -1.25, 0.35], rotationY: 0, width: 460, height: 220, standing: false },
  // standing, near
  { id: "log", position: [-6.5, 0.75, 2.2], rotationY: 0.5, width: 430, height: 400, standing: true },
  { id: "activity", position: [-5.7, -1.5, 4.0], rotationY: 0.4, width: 470, height: 215, standing: true },
  { id: "terminals", position: [6.2, -1.45, 3.6], rotationY: -0.46, width: 490, height: 225, standing: true },
  // the headline at the mascot's feet
  { id: "headline", position: [0, -2.3, 4.9], rotationY: 0, width: 560, height: 70, standing: true },
];

/** The world height of a panel's bottom edge — every panel must stand on or above the floor. */
export function panelBottom(p: RoomPanel): number {
  return p.position[1] - (p.height * PANEL_SCALE) / 2;
}

/**
 * The back wall's size so it fills the camera's view at the wall's plane:
 * the visible height at distance `d` for a vertical field of view `fov`,
 * and that height times the viewport's aspect, with a little overscan so
 * the sway never shows the wall's edge.
 */
export function wallSize(aspect: number, overscan = 1.12): { width: number; height: number } {
  const d = CAMERA.position[2];
  const height = 2 * d * Math.tan((CAMERA.fov * Math.PI) / 360) * overscan;
  return { width: round(height * aspect, 3), height: round(height, 3) };
}

/**
 * `background-size: cover` for a texture on a plane: the repeat and offset
 * that show the picture uncropped on the longer axis and crop it centred on
 * the other, whatever the two aspect ratios are.
 */
export function coverFit(
  imageAspect: number,
  planeAspect: number,
): { repeat: [number, number]; offset: [number, number] } {
  if (imageAspect <= 0 || planeAspect <= 0) return { repeat: [1, 1], offset: [0, 0] };
  if (imageAspect > planeAspect) {
    // picture wider than the plane: crop the sides
    const rx = planeAspect / imageAspect;
    return { repeat: [round(rx, 4), 1], offset: [round((1 - rx) / 2, 4), 0] };
  }
  // picture taller than the plane: crop top and bottom
  const ry = imageAspect / planeAspect;
  return { repeat: [1, round(ry, 4)], offset: [0, round((1 - ry) / 2, 4)] };
}

/**
 * The camera's target position for a pointer at (px, py) in −1…1: it moves
 * WITH the pointer, gently, so the case is seen a little from the side you
 * lean to — and the planes in front slide against the wall behind them.
 */
export function cameraTarget(px: number, py: number): [number, number, number] {
  const [x, y, z] = CAMERA.position;
  return [round(x + clamp(px) * CAMERA.swayX, 3), round(y + clamp(py) * CAMERA.swayY, 3), z];
}

/** An exponential lerp factor for one frame of `dt` seconds at `CAMERA.follow`. */
export function followAlpha(dt: number): number {
  return 1 - Math.exp(-CAMERA.follow * Math.max(0, dt));
}

function clamp(v: number): number {
  return Math.min(1, Math.max(-1, v));
}

function round(v: number, places: number): number {
  const f = 10 ** places;
  return Math.round(v * f) / f;
}
