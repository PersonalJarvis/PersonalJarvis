import { validBuildingTurn, invalidateNavigation } from "./navigation";
import { getMotionWorld } from "./locomotion";
/**
 * The headings a viewer has given the island's buildings — the one store the
 * rotate handle writes and every turned building reads.
 *
 * A building rests at the heading `islandLayout.ts` designs for it; dragging
 * its handle overrides that heading here, "reset" removes the override. Every
 * change is pushed straight into the built island (`applyBuildingYaws`), so
 * the walkers route around the building where it now stands, and persisted
 * per viewer in localStorage: cosmetic, local, never synced (MASTERPLAN §2.7 —
 * pixels are the client's business).
 */
import { create } from "zustand";

import {
  applyBuildingYaws,
  buildIsland,
  defaultBuildingYaw,
  normalizeAngle,
  type BuildingId,
} from "./islandLayout";

const KEY = "jarvis.world.poses.v1";

/** Overrides closer than this to the default snap back to it (about 4°). */
export const SNAP_TO_DEFAULT_RAD = 0.07;
/** Step the handle snaps to while Shift is held. */
export const SNAP_STEP_RAD = Math.PI / 12;

export type YawOverrides = Partial<Record<BuildingId, number>>;

export interface BuildingPoseState {
  /** Heading per building the viewer has turned; absent = the designed heading. */
  yaw: YawOverrides;
  /** Bumped on every change — a walker mid-route re-plans when it sees a new value. */
  generation: number;
  /** The building whose handle is showing (a house; ring hubs show theirs while their drawer is open). */
  selected: BuildingId | null;
  /** True while a handle is being dragged — figure clicks and camera drags stand down. */
  rotating: boolean;
  rejected: BuildingId | null;
  setYaw: (id: BuildingId, yaw: number, snapStep?: boolean) => void;
  resetYaw: (id: BuildingId) => void;
  resetAll: () => void;
  select: (id: BuildingId | null) => void;
  setRotating: (rotating: boolean) => void;
}

function read(): YawOverrides {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as { yaw?: unknown };
    return normalizeOverrides(parsed?.yaw);
  } catch {
    return {};
  }
}

function write(yaw: YawOverrides): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ yaw }));
  } catch {
    /* not persisted, still applied */
  }
}

/** Keep only well-formed entries: a known id shape and a finite angle. */
export function normalizeOverrides(v: unknown): YawOverrides {
  if (!v || typeof v !== "object") return {};
  const out: YawOverrides = {};
  for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
    if (!/^(house:\d{1,2}|kit:[a-z]+)$/.test(k)) continue;
    if (typeof val !== "number" || !Number.isFinite(val)) continue;
    out[k as BuildingId] = normalizeAngle(val);
  }
  return out;
}

/**
 * The heading a drag lands on: snapped to the designed heading when close,
 * to 15° steps while Shift is held, free otherwise.
 */
export function settleYaw(id: BuildingId, raw: number, snapStep: boolean): number {
  const fallback = defaultBuildingYaw(id);
  let yaw = normalizeAngle(raw);
  if (snapStep) yaw = normalizeAngle(Math.round(yaw / SNAP_STEP_RAD) * SNAP_STEP_RAD);
  if (Math.abs(normalizeAngle(yaw - fallback)) < SNAP_TO_DEFAULT_RAD) return fallback;
  return yaw;
}

const initial = typeof localStorage === "undefined" ? {} : read();

export const useBuildingPoses = create<BuildingPoseState>((set, get) => ({
  yaw: initial,
  generation: 0,
  selected: null,
  rotating: false,
  rejected: null,
  setYaw: (id, raw, snapStep = false) => {
    const yaw = settleYaw(id, raw, snapStep);
    if (!validBuildingTurn(buildIsland(), id, yaw, getMotionWorld()?.actors.values() ?? [])) { set({ rejected: id }); return; }
    set({ rejected: null });
    const next = { ...get().yaw };
    if (yaw === defaultBuildingYaw(id)) delete next[id];
    else next[id] = yaw;
    commit(next, set, get);
  },
  resetYaw: (id) => {
    if (id in get().yaw) get().setYaw(id, defaultBuildingYaw(id));
  },
  resetAll: () => {
    for (const id of Object.keys(get().yaw) as BuildingId[]) get().resetYaw(id);
  },
  select: (selected) => set({ selected }),
  setRotating: (rotating) => set({ rotating }),
}));

function commit(
  yaw: YawOverrides,
  set: (partial: Partial<BuildingPoseState>) => void,
  get: () => BuildingPoseState,
): void {
  applyBuildingYaws(buildIsland(), yaw);
  invalidateNavigation();
  set({ yaw, generation: get().generation + 1 });
  write(yaw);
}

/** Push the stored overrides into the island once it exists — the stage calls this on mount. */
export function syncBuildingPoses(): void {
  const stored = useBuildingPoses.getState().yaw;
  const safe: YawOverrides = {};
  for (const [id, yaw] of Object.entries(stored)) {
    if (yaw !== undefined && validBuildingTurn(buildIsland(), id as BuildingId, yaw, [])) {
      safe[id as BuildingId] = yaw;
      applyBuildingYaws(buildIsland(), safe);
    }
  }
  useBuildingPoses.setState({ yaw: safe });
  invalidateNavigation();
}

/** The heading a building shows right now: the viewer's, else the designed one. */
export function useBuildingYaw(id: BuildingId): number {
  return useBuildingPoses((s) => s.yaw[id] ?? defaultBuildingYaw(id));
}

/** Whether the viewer has turned this building away from its designed heading. */
export function useIsTurned(id: BuildingId): boolean {
  return useBuildingPoses((s) => id in s.yaw);
}

/** How many buildings the viewer has turned. */
export function useTurnedCount(): number {
  return useBuildingPoses((s) => Object.keys(s.yaw).length);
}
