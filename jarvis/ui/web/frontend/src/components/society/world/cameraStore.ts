/**
 * Where the viewer is looking: the camera target on the ground and the zoom
 * step. One store per page — the stage, the minimap and the HUD all read it,
 * the controls write it. Pixels are the client's business (MASTERPLAN §2.7),
 * so nothing here is ever persisted or synced.
 */
import { create } from "zustand";

import { DEFAULT_ZOOM, MAX_ZOOM, clampTarget, stepZoom, type ZoomLevel } from "./worldCamera";

export interface CameraState {
  /** Ground point the camera looks at, world metres. */
  target: [number, number];
  zoom: ZoomLevel;
  /** True while the pointer drags the world — figures ignore clicks then. */
  dragging: boolean;
  /** Keys currently held for keyboard panning (`ArrowUp`, `KeyW`, …). */
  heldKeys: Set<string>;
  /** Bumped when the viewport size or aspect changes — the minimap re-reads. */
  aspect: number;
  panBy: (dx: number, dz: number) => void;
  jumpTo: (x: number, z: number) => void;
  /** Look at a spot at a chosen zoom — the world's "show me this". */
  focusOn: (x: number, z: number, zoom: ZoomLevel) => void;
  zoomStep: (direction: 1 | -1) => void;
  setDragging: (dragging: boolean) => void;
  setAspect: (aspect: number) => void;
  keyDown: (code: string) => void;
  keyUp: (code: string) => void;
  clearKeys: () => void;
}

/**
 * `?world=x,z[,zoom]` deep-links a spot on the island (metres from the centre,
 * zoom step 0–4) — for "show me the harbor" from voice or a shared link, and
 * for screenshots. Anything malformed falls back to the square.
 */
export function focusFromSearch(search: string): { target: [number, number]; zoom: ZoomLevel } | null {
  const raw = new URLSearchParams(search).get("world");
  if (!raw) return null;
  const parts = raw.split(",").map((v) => Number(v));
  if (parts.length < 2 || !parts.slice(0, 2).every(Number.isFinite)) return null;
  const target = clampTarget(parts[0], parts[1]);
  const z =
    parts.length > 2 && Number.isInteger(parts[2]) && parts[2] >= 0 && parts[2] <= MAX_ZOOM
      ? (parts[2] as ZoomLevel)
      : DEFAULT_ZOOM;
  return { target, zoom: z };
}

const initialFocus =
  typeof window !== "undefined" ? focusFromSearch(window.location.search) : null;

export const useCameraStore = create<CameraState>((set, get) => ({
  target: initialFocus?.target ?? [0, 0],
  zoom: initialFocus?.zoom ?? DEFAULT_ZOOM,
  dragging: false,
  heldKeys: new Set<string>(),
  aspect: 16 / 9,
  panBy: (dx, dz) => {
    const [x, z] = get().target;
    set({ target: clampTarget(x + dx, z + dz) });
  },
  jumpTo: (x, z) => set({ target: clampTarget(x, z) }),
  focusOn: (x, z, zoom) => set({ target: clampTarget(x, z), zoom }),
  zoomStep: (direction) => set((s) => ({ zoom: stepZoom(s.zoom, direction) })),
  setDragging: (dragging) => set({ dragging }),
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
