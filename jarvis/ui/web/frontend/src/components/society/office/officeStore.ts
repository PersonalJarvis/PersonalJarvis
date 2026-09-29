/**
 * Office interaction state shared between the canvas and the DOM HUD.
 *
 * Positions that change every frame live in the mutable `player` object and
 * are never React state; the store only carries what the HUD renders or what
 * the walkers must react to (selection, nearby target, calls, gatherings).
 */
import { create } from "zustand";
import type { CheckpointKind, Point } from "./officeLayout";

/** `arcade` is a playable arcade cabinet, by furniture id. */
export type Selection = { kind: "agent"; id: string } | { kind: "checkpoint"; id: CheckpointKind } | { kind: "arcade"; id: string };

export interface PlayerBody { x: number; z: number; heading: number; path: Point[]; moving: boolean }

/** Agents walking over to a point for a while (called by the person, or a team gathering). */
export interface Summon { target: Point; untilMs: number }

interface OfficeState {
  selection: Selection | null;
  /** The closest thing the character can interact with (E), or null. */
  nearby: Selection | null;
  /** Camera follows the character; any manual pan turns it off, moving turns it back on. */
  follow: boolean;
  /** One-shot camera fly-to request, consumed by the camera rig. */
  focus: { point: Point; seq: number } | null;
  summons: Record<string, Summon>;
  /** One-shot camera dive into a desk monitor (then the chat opens), consumed by the camera rig. */
  zoom: { target: [number, number, number]; facing: number; seq: number } | null;
  /** Dive into a monitor centred on `target`, whose screen faces yaw `facing`. */
  zoomInto: (target: [number, number, number], facing: number) => void;
  /** Walk the character to this point (click-to-move / "walk there"), consumed by the player. */
  walkTo: { point: Point; seq: number } | null;
  /** Agents picked for a new team, carried from agent panels to the team room. */
  teamDraft: string[];
  toggleDraft: (agentId: string) => void;
  clearDraft: () => void;
  clearSummons: () => void;
  select: (selection: Selection | null) => void;
  setNearby: (nearby: Selection | null) => void;
  setFollow: (follow: boolean) => void;
  focusOn: (point: Point) => void;
  summon: (agentIds: readonly string[], target: Point, durationMs: number) => void;
  requestWalk: (point: Point) => void;
}

let seq = 0;

export const useOfficeStore = create<OfficeState>((set) => ({
  selection: null,
  nearby: null,
  follow: true,
  focus: null,
  summons: {},
  walkTo: null,
  zoom: null,
  zoomInto: (target, facing) => set({ zoom: { target, facing, seq: ++seq }, follow: false }),
  teamDraft: [],
  toggleDraft: (agentId) => set((s) => ({
    teamDraft: s.teamDraft.includes(agentId) ? s.teamDraft.filter((id) => id !== agentId) : [...s.teamDraft, agentId],
  })),
  clearDraft: () => set({ teamDraft: [] }),
  clearSummons: () => set({ summons: {} }),
  select: (selection) => set({ selection }),
  setNearby: (nearby) => set((s) => (sameSelection(s.nearby, nearby) ? s : { nearby })),
  setFollow: (follow) => set((s) => (s.follow === follow ? s : { follow })),
  focusOn: (point) => set({ focus: { point, seq: ++seq }, follow: false }),
  summon: (agentIds, target, durationMs) => set((s) => {
    const now = Date.now();
    const untilMs = now + durationMs;
    // Expired calls are dropped here, so the map never grows with old calls.
    const next = Object.fromEntries(Object.entries(s.summons).filter(([, summon]) => summon.untilMs > now));
    agentIds.forEach((id, i) => {
      // Fan the group out around the target so nobody stands inside anybody.
      const angle = (i / Math.max(1, agentIds.length)) * Math.PI * 2;
      const r = agentIds.length > 1 ? 0.9 + 0.15 * agentIds.length : 1.2;
      next[id] = { target: { x: target.x + Math.sin(angle) * r, z: target.z + Math.cos(angle) * r }, untilMs };
    });
    return { summons: next };
  }),
  requestWalk: (point) => set({ walkTo: { point, seq: ++seq }, follow: true }),
}));

/** Where the camera looks on the floor, mutated by the camera rig every frame (for the minimap). */
export const cameraView = { x: 0, z: 0, yaw: 0, halfWidth: 0.5, ready: false };

/**
 * What the office remembers while the app runs: where the character stands
 * and how the camera looked. Leaving the map (e.g. into an agent's chat) and
 * coming back puts you where you were; only an app reload starts fresh.
 */
export const officeSession: {
  playerPlaced: boolean;
  camera: { position: [number, number, number]; target: [number, number, number] } | null;
  follow: boolean;
} = { playerPlaced: false, camera: null, follow: true };

/** The character's body, mutated by the player controller every frame. */
export const player: PlayerBody = { x: 0, z: 0, heading: Math.PI, path: [], moving: false };

export function sameSelection(a: Selection | null, b: Selection | null): boolean {
  return a === b || (!!a && !!b && a.kind === b.kind && a.id === b.id);
}
