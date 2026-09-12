import { WORLD, WORLD_BOUNDS, type Vec3 } from "./world";

export const VIEWPOINTS = ["reference", "front", "rear", "left", "right", "top"] as const;
export type Viewpoint = typeof VIEWPOINTS[number];
export type CameraMode = "overview" | "outpost" | "orbit" | "player";
export interface CameraPose { position: Vec3; target: Vec3 }
export interface ViewPreferences {
  mode: CameraMode;
  viewpoint: Viewpoint;
  neutral: boolean;
  shadows: boolean;
  pose: CameraPose | null;
}
const DEFAULTS: ViewPreferences = { mode: "overview", viewpoint: "reference", neutral: false, shadows: true, pose: null };
export const VIEW_KEY = `jarvis.${WORLD.world_id}.view.v${WORLD.layout_version}`;
export const VIEW_DIRECTIONS: Record<Viewpoint, Vec3> = {
  reference: [0.8, 0.9, 1], front: [0, 0.28, 1], rear: [0, 0.28, -1],
  left: [-1, 0.28, 0], right: [1, 0.28, 0], top: [0, 1, 0.04],
};

/** Client-owned view state never enters the authoritative task or journey records. */
export function parseViewPreferences(raw: string | null): ViewPreferences {
  try {
    const value = JSON.parse(raw ?? "null");
    if (!value || value.world_id !== WORLD.world_id || value.layout_version !== WORLD.layout_version
      || !["overview", "outpost", "orbit", "player"].includes(value.mode)
      || !VIEWPOINTS.includes(value.viewpoint) || typeof value.neutral !== "boolean") return { ...DEFAULTS };
    const vector = (v: unknown): v is Vec3 => Array.isArray(v) && v.length === 3
      && v.every((n) => typeof n === "number" && Number.isFinite(n) && Math.abs(n) <= 20000);
    const pose = value.pose;
    const validPose = pose && vector(pose.position) && vector(pose.target)
      && pose.target.every((n: number, axis: number) => n >= WORLD_BOUNDS.min[axis] && n <= WORLD_BOUNDS.max[axis])
      && Math.hypot(...pose.position.map((n: number, axis: number) => n - pose.target[axis])) >= 0.5;
    return { mode: value.mode === "orbit" && !validPose ? "overview" : value.mode,
      viewpoint: value.viewpoint, neutral: value.neutral, shadows: typeof value.shadows === "boolean" ? value.shadows : true, pose: validPose ? pose : null };
  } catch {
    // Corrupt or older client preferences must never prevent opening the world.
    return { ...DEFAULTS };
  }
}

export function readViewPreferences(): ViewPreferences {
  try { return parseViewPreferences(localStorage.getItem(VIEW_KEY)); }
  catch { return { ...DEFAULTS }; } // Storage denial only disables view restoration.
}

export function saveViewPreferences(value: ViewPreferences): void {
  try { localStorage.setItem(VIEW_KEY, JSON.stringify({ ...value, world_id: WORLD.world_id, layout_version: WORLD.layout_version })); }
  catch { /* In-memory controls remain usable when client storage is unavailable. */ }
}
