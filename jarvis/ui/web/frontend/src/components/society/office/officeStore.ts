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

/** The two floors of the building: the Jarvis agents office and, one elevator ride up, the coding agents. */
export type OfficeFloor = "agents" | "coding";

export const OFFICE_FLOORS: readonly OfficeFloor[] = ["agents", "coding"];

export function otherFloor(floor: OfficeFloor): OfficeFloor {
  return floor === "agents" ? "coding" : "agents";
}

export interface PlayerBody { x: number; z: number; heading: number; path: Point[]; moving: boolean }

/** Agents walking over to a point for a while (called by the person, or a team gathering). */
export interface Summon {
  target: Point;
  untilMs: number;
  /** A team meeting's chair (a `meeting-*` spot id): the agent sits there instead of standing by `target`. */
  spotId?: string;
}

/** A team meeting at the team-room table; its members hold summons with a chair each until it ends. */
export interface TeamMeeting { groupId: string; name: string; members: string[]; untilMs: number }

interface OfficeState {
  /** The floor on screen; lives here so leaving the map and coming back keeps it. */
  floor: OfficeFloor;
  /**
   * Switch floors: forgets panels, the nearby target, calls and a pending walk
   * (they belong to the floor that was left). The character's position is the
   * job of `switchFloor` + the scene, because only the new floor plan knows the elevator.
   */
  setFloor: (floor: OfficeFloor) => void;
  selection: Selection | null;
  /** The closest thing the character can interact with (E), or null. */
  nearby: Selection | null;
  /** Camera follows the character; any manual pan turns it off, moving turns it back on. */
  follow: boolean;
  /** One-shot camera fly-to request, consumed by the camera rig. */
  focus: { point: Point; seq: number } | null;
  summons: Record<string, Summon>;
  /** The meeting running in the team room, if any; one at a time. */
  meeting: TeamMeeting | null;
  /** Seat a team at the table (replacing any running meeting), each member on the chair `seats` gives it. */
  startMeeting: (meeting: TeamMeeting, seats: Record<string, { target: Point; spotId?: string }>) => void;
  /** End the running meeting: its members go back to their day. */
  endMeeting: () => void;
  /** One-shot camera dive into a desk monitor (then the chat opens), consumed by the camera rig. */
  zoom: { target: [number, number, number]; facing: number; size?: [number, number]; seq: number } | null;
  /** Dive into a monitor centred on `target`, whose screen faces yaw `facing`; `size` is the screen in metres (default: a desk monitor). */
  zoomInto: (target: [number, number, number], facing: number, size?: [number, number]) => void;
  /** One-shot: a Mission Control monitor was clicked; the stage dives in, then opens this app section. */
  sectionDive: { section: MonitorSection; seq: number } | null;
  /** Dive into a Mission Control monitor, then open the app section it shows. */
  diveToSection: (section: MonitorSection, target: [number, number, number], facing: number, size: [number, number]) => void;
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

/** The app sections Mission Control's desk monitors show live. */
export type MonitorSection = "costs" | "agents" | "agentic-ide";

let seq = 0;

export const useOfficeStore = create<OfficeState>((set) => ({
  floor: "agents",
  setFloor: (floor) => set((s) => (s.floor === floor ? s : {
    floor, selection: null, nearby: null, summons: {}, meeting: null, walkTo: null, zoom: null, sectionDive: null, focus: null, teamDraft: [], follow: true,
  })),
  selection: null,
  nearby: null,
  follow: true,
  focus: null,
  summons: {},
  meeting: null,
  startMeeting: (meeting, seats) => set((s) => {
    const now = Date.now();
    const ended = new Set(s.meeting?.members ?? []);
    const next = Object.fromEntries(Object.entries(s.summons).filter(([id, summon]) => summon.untilMs > now && !ended.has(id)));
    for (const [id, seat] of Object.entries(seats)) next[id] = { ...seat, untilMs: meeting.untilMs };
    return { meeting, summons: next };
  }),
  endMeeting: () => set((s) => {
    if (!s.meeting) return s;
    const members = new Set(s.meeting.members);
    return { meeting: null, summons: Object.fromEntries(Object.entries(s.summons).filter(([id]) => !members.has(id))) };
  }),
  walkTo: null,
  zoom: null,
  zoomInto: (target, facing, size) => set({ zoom: { target, facing, size, seq: ++seq }, follow: false }),
  sectionDive: null,
  diveToSection: (section, target, facing, size) => set({
    zoom: { target, facing, size, seq: ++seq }, sectionDive: { section, seq: ++seq }, follow: false, selection: null,
  }),
  teamDraft: [],
  toggleDraft: (agentId) => set((s) => ({
    teamDraft: s.teamDraft.includes(agentId) ? s.teamDraft.filter((id) => id !== agentId) : [...s.teamDraft, agentId],
  })),
  clearDraft: () => set({ teamDraft: [] }),
  clearSummons: () => set({ summons: {}, meeting: null }),
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

/**
 * Where the camera looks on the floor, mutated by the camera rig every frame (for the minimap).
 * `firstPerson` is true while the camera is (or glides into) the character's eyes at Mission Control:
 * whatever flies beside the character, like Gigi, must step out of that view.
 */
export const cameraView = { x: 0, z: 0, yaw: 0, halfWidth: 0.5, ready: false, firstPerson: false };

/**
 * What the office remembers while the app runs: where the character stands
 * and how the camera looked. Leaving the map (e.g. into an agent's chat) and
 * coming back puts you where you were; only an app reload starts fresh.
 */
export const officeSession: {
  playerPlaced: boolean;
  camera: { position: [number, number, number]; target: [number, number, number] } | null;
  follow: boolean;
  /** Where the character stood on each floor when it last left it. */
  floors: Partial<Record<OfficeFloor, { x: number; z: number; heading: number }>>;
  /**
   * A pending arrival: the next floor plan of `floor` places the character
   * (at the elevator after a ride, else where it last stood there).
   */
  arrival: { floor: OfficeFloor; at: "elevator" | "remembered" } | null;
} = { playerPlaced: false, camera: null, follow: true, floors: {}, arrival: null };

/**
 * Leave the current floor for `to`: remember where the character stands, then
 * switch. `ride` = the elevator (arrive at its doors); otherwise (a mount that
 * asks for another floor) the character returns to where it last stood there.
 */
export function switchFloor(to: OfficeFloor, ride: boolean): void {
  const store = useOfficeStore.getState();
  if (store.floor === to) return;
  if (officeSession.playerPlaced) officeSession.floors[store.floor] = { x: player.x, z: player.z, heading: player.heading };
  officeSession.arrival = { floor: to, at: ride || !officeSession.floors[to] ? "elevator" : "remembered" };
  player.path = [];
  player.moving = false;
  store.setFloor(to);
}

/** The character's body, mutated by the player controller every frame. */
export const player: PlayerBody = { x: 0, z: 0, heading: Math.PI, path: [], moving: false };

export function sameSelection(a: Selection | null, b: Selection | null): boolean {
  return a === b || (!!a && !!b && a.kind === b.kind && a.id === b.id);
}
