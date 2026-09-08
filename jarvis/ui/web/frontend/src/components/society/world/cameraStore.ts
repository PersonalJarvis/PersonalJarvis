/**
 * Where the viewer is looking: the camera target on the ground, the zoom step
 * and the two orbit angles. One store per page — the stage, the minimap, the
 * sun and the HUD all read it, the controls write it. Pixels are the client's
 * business (MASTERPLAN §2.7), so nothing here is ever persisted or synced;
 * a view worth keeping travels as a `?world=` link instead.
 */
import { create } from "zustand";

import {
  CAMERA_PITCH_DEG,
  CAMERA_YAW_DEG,
  DEFAULT_ZOOM,
  MAX_ZOOM,
  snapViewYaw,
  clampTarget,
  stepYaw,
  stepZoom,
  type ZoomLevel,
} from "./worldCamera";

export interface CameraState {
  /** Ground point the camera looks at, world metres. */
  target: [number, number];
  zoom: ZoomLevel;
  /** Compass direction the camera stands in, degrees in [0, 360). */
  yaw: number;
  /** Angle below the horizon, degrees between MIN_PITCH_DEG and MAX_PITCH_DEG. */
  pitch: number;
  /** True while the pointer drags the world — figures ignore clicks then. */
  dragging: boolean;
  /** True while the pointer orbits the view — the angles then track it with no ease. */
  orbiting: boolean;
  /** Keys currently held for keyboard panning (`ArrowUp`, `KeyW`, …). */
  heldKeys: Set<string>;
  /** Bumped when the viewport size or aspect changes — the minimap re-reads. */
  aspect: number;
  panBy: (dx: number, dz: number) => void;
  jumpTo: (x: number, z: number) => void;
  /** Look at a spot at a chosen zoom — the world's "show me this". */
  focusOn: (x: number, z: number, zoom: ZoomLevel) => void;
  zoomStep: (direction: 1 | -1) => void;
  /** Turn the view by a pointer drag: yaw wraps, pitch is clamped. */
  orbitBy: (dYaw: number, dPitch: number) => void;
  /** One quarter turn left (−1) or right (+1) — the keyboard and the compass. */
  turnYaw: (direction: 1 | -1) => void;
  /** Back to the designed bird's-eye view; the target and the zoom stay put. */
  resetView: () => void;
  setDragging: (dragging: boolean) => void;
  setOrbiting: (orbiting: boolean) => void;
  setAspect: (aspect: number) => void;
  keyDown: (code: string) => void;
  keyUp: (code: string) => void;
  clearKeys: () => void;
}

/**
 * `?world=x,z[,zoom[,yaw[,pitch]]]` deep-links a view of the island (metres
 * from the centre, zoom step 0–4, then the two orbit angles in degrees) — for
 * "show me the harbor" from voice or a shared link, and for screenshots that
 * need a building's back. Anything malformed falls back to the designed view.
 */
export function focusFromSearch(
  search: string,
): { target: [number, number]; zoom: ZoomLevel; yaw: number; pitch: number } | null {
  const raw = new URLSearchParams(search).get("world");
  if (!raw) return null;
  const parts = raw.split(",").map((v) => Number(v));
  if (parts.length < 2 || !parts.slice(0, 2).every(Number.isFinite)) return null;
  const target = clampTarget(parts[0], parts[1]);
  const z =
    parts.length > 2 && Number.isInteger(parts[2]) && parts[2] >= 0 && parts[2] <= MAX_ZOOM
      ? (parts[2] as ZoomLevel)
      : DEFAULT_ZOOM;
  const yaw = parts.length > 3 ? snapViewYaw(parts[3]) : CAMERA_YAW_DEG;
  const pitch = CAMERA_PITCH_DEG;
  return { target, zoom: z, yaw, pitch };
}

const initialFocus =
  typeof window !== "undefined" ? focusFromSearch(window.location.search) : null;

export const useCameraStore = create<CameraState>((set, get) => ({
  target: initialFocus?.target ?? [0, 0],
  zoom: initialFocus?.zoom ?? DEFAULT_ZOOM,
  yaw: initialFocus?.yaw ?? CAMERA_YAW_DEG,
  pitch: initialFocus?.pitch ?? CAMERA_PITCH_DEG,
  dragging: false,
  orbiting: false,
  heldKeys: new Set<string>(),
  aspect: 16 / 9,
  panBy: (dx, dz) => {
    const [x, z] = get().target;
    set({ target: clampTarget(x + dx, z + dz) });
  },
  jumpTo: (x, z) => set({ target: clampTarget(x, z) }),
  focusOn: (x, z, zoom) => set({ target: clampTarget(x, z), zoom }),
  zoomStep: (direction) => set((s) => ({ zoom: stepZoom(s.zoom, direction) })),
  orbitBy: (dYaw) =>
    set((s) => ({ yaw: snapViewYaw(s.yaw + dYaw), pitch: CAMERA_PITCH_DEG })),
  turnYaw: (direction) => set((s) => ({ yaw: stepYaw(s.yaw, direction) })),
  resetView: () => set({ yaw: CAMERA_YAW_DEG, pitch: CAMERA_PITCH_DEG }),
  setDragging: (dragging) => set({ dragging }),
  setOrbiting: (orbiting) => set({ orbiting }),
  setAspect: (aspect) => set({ aspect }),
  keyDown: (code) => {
    const keys = get().heldKeys;
    if (keys.has(code)) return;
    const next = new Set(keys);
    next.add(code);
    set({ heldKeys: next });
  },
  keyUp: (code) => {
    const keys = get().heldKeys;
    if (!keys.has(code)) return;
    const next = new Set(keys);
    next.delete(code);
    set({ heldKeys: next });
  },
  clearKeys: () => set({ heldKeys: new Set<string>() }),
}));

/** Screen-space pan direction for the held keys: [right, up] in −1..1. */
export function keyPanVector(keys: ReadonlySet<string>): [number, number] {
  let right = 0;
  let up = 0;
  if (keys.has("ArrowLeft") || keys.has("KeyA")) right -= 1;
  if (keys.has("ArrowRight") || keys.has("KeyD")) right += 1;
  if (keys.has("ArrowUp") || keys.has("KeyW")) up += 1;
  if (keys.has("ArrowDown") || keys.has("KeyS")) up -= 1;
  return [right, up];
}
