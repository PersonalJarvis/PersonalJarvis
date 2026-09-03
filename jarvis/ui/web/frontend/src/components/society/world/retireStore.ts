/**
 * Which agent is being retired right now, and what the ceremony is doing to
 * the figures on the island — the state half of `retirement.ts`.
 *
 * Two channels, on purpose:
 *
 *  - the zustand store carries the ceremony itself (who, which beat, is one
 *    running at all). It changes about a dozen times over twenty seconds, so
 *    a React render per change costs nothing and the HUD can read it;
 *  - the pose map is a plain mutable `Map`, written and read inside
 *    `useFrame`, exactly like `walkerRegistry.ts`. A walker under the
 *    ceremony's control reads its pose every frame; re-rendering React at
 *    60 fps to move a figure would be absurd.
 *
 * The commit — the actual `DELETE /api/society/agents/{id}` — is handed in by
 * whoever started the ceremony and is fired ONCE, at the end. If the ceremony
 * cannot run or cannot finish (no WebGL, reduced motion, the canvas torn down
 * mid-execution, a path that never resolves), the commit still fires: someone
 * who pressed Retire gets their agent retired, animation or no animation.
 */
import { create } from "zustand";

import type { AgentPalette } from "../data";
import type { FigureRecipe } from "../figures/figureRecipe";
import type { WalkerMode } from "./WalkerFigure";
import type { RetirePhase } from "./retirement";

/**
 * A ceremony that has run this long has hit something it cannot finish (an
 * unreachable body, a path that never resolves). It is cut short and the
 * agent is retired anyway.
 */
export const CEREMONY_TIMEOUT_MS = 90_000;

/**
 * A ceremony nobody picked up in this long has no stage to play on — the
 * viewer is on the ledger, the canvas never mounted, WebGL is gone. The
 * agent is retired without the show rather than sitting in limbo.
 */
export const ATTACH_GRACE_MS = 2_000;

/** The pose the ceremony forces onto a walker; null means "you are free". */
export interface WalkerOverride {
  x: number;
  z: number;
  /** Height of the figure's origin above the ground plane, in metres. */
  y: number;
  heading: number;
  /** Rotation about the figure's own X axis — the topple. 0 while upright. */
  pitch: number;
  mode: WalkerMode;
  /** Ground speed for the stride rate, m/s. */
  speed: number;
  /** The body has been handed to the bearers: the walker stops drawing itself. */
  hidden: boolean;
  /** How far the rifle is up, 0..1; only the executioner ever gets this. */
  aim: number;
  /** Muzzle-flash brightness this frame, 0..1. */
  flash: number;
}

export interface Ceremony {
  /** The agent being retired. */
  agentId: string;
  name: string;
  /** Kept so the body can be drawn after the roster row is gone. */
  figure: FigureRecipe | null;
  palette: AgentPalette;
  /** Who does it — the lead's agent id, or null when the lead is not on the island. */
  executionerId: string | null;
  startedMs: number;
  /** Fired once, when the body is in the mine (or when the ceremony is cut short). */
  commit: () => void | Promise<void>;
}

interface RetireState {
  ceremony: Ceremony | null;
  phase: RetirePhase;
  /**
   * Start the ceremony. Returns false when one is already running — two
   * executions at once would fight over the same lead.
   */
  begin: (ceremony: Omit<Ceremony, "startedMs">) => boolean;
  /** The stage picked the ceremony up: it will be played, not cut short. */
  attach: () => void;
  setPhase: (phase: RetirePhase) => void;
  /** The body is in the mine: commit the deletion and clear the stage. */
  finish: () => void;
  /**
   * The ceremony cannot run or cannot finish. The agent is retired anyway —
   * pressing Retire must not depend on a working canvas.
   */
  cutShort: () => void;
}

/** Per-frame poses, keyed by agent id. Mutable on purpose; see the file docstring. */
const overrides = new Map<string, WalkerOverride>();

/**
 * Agents whose body is already in the mine. The deletion is committed the
 * instant the body lands, but the roster refetch that removes the row takes a
 * moment longer — without this the walker would pop back onto the plaza and
 * stand there, alive, for a second. The entry is dropped when the walker
 * finally unmounts.
 */
const buried = new Set<string>();

/** The pose a buried figure holds: nowhere, and not drawn. */
const BURIED_POSE: WalkerOverride = {
  x: 0,
  y: 0,
  z: 0,
  heading: 0,
  pitch: 0,
  mode: "rest",
  speed: 0,
  hidden: true,
  aim: 0,
  flash: 0,
};

/** Watchdogs for the running ceremony; cleared whenever it ends. */
let attachTimer: ReturnType<typeof setTimeout> | null = null;
let capTimer: ReturnType<typeof setTimeout> | null = null;

function clearTimers(): void {
  if (attachTimer !== null) clearTimeout(attachTimer);
  if (capTimer !== null) clearTimeout(capTimer);
  attachTimer = null;
  capTimer = null;
}

export const useRetireStore = create<RetireState>((set, get) => ({
  ceremony: null,
  phase: "done",
  begin: (ceremony) => {
    if (get().ceremony !== null) return false;
    overrides.clear();
    clearTimers();
    set({ ceremony: { ...ceremony, startedMs: Date.now() }, phase: "march" });
    // Nothing may leave an agent half-retired: if no stage picks this up, or
    // the ceremony wedges on an unreachable body, the deletion still lands.
    attachTimer = setTimeout(() => {
      attachTimer = null;
      if (get().ceremony !== null) get().cutShort();
    }, ATTACH_GRACE_MS);
    capTimer = setTimeout(() => {
      capTimer = null;
      if (get().ceremony !== null) get().cutShort();
    }, CEREMONY_TIMEOUT_MS);
    return true;
  },
  attach: () => {
    if (attachTimer === null) return;
    clearTimeout(attachTimer);
    attachTimer = null;
  },
  setPhase: (phase) => {
    if (get().phase === phase) return;
    set({ phase });
  },
  finish: () => {
    const active = get().ceremony;
    overrides.clear();
    clearTimers();
    set({ ceremony: null, phase: "done" });
    void active?.commit();
  },
  cutShort: () => {
    const active = get().ceremony;
    overrides.clear();
    clearTimers();
    set({ ceremony: null, phase: "done" });
    void active?.commit();
  },
}));

/** Start a retirement. False when one is already under way. */
export function beginRetirement(ceremony: Omit<Ceremony, "startedMs">): boolean {
  return useRetireStore.getState().begin(ceremony);
}

/** Called by the stage the moment it takes the ceremony on. */
export function attachRetirement(): void {
  useRetireStore.getState().attach();
}

/** True while any agent is being retired. */
export function retirementRunning(): boolean {
  return useRetireStore.getState().ceremony !== null;
}

/** The pose the ceremony wants this walker in, or null when it is free. */
export function retirementPose(agentId: string): WalkerOverride | null {
  const live = overrides.get(agentId);
  if (live) return live;
  return buried.has(agentId) ? BURIED_POSE : null;
}

/** The body is in the mine: keep the figure off the island until its row goes. */
export function buryRetired(agentId: string): void {
  buried.add(agentId);
}

/** The walker unmounted — the roster row is gone, nothing left to hide. */
export function forgetRetired(agentId: string): void {
  buried.delete(agentId);
}

/** Written by the ceremony each frame for the figures it drives. */
export function setRetirementPose(agentId: string, pose: WalkerOverride): void {
  overrides.set(agentId, pose);
}

/** Hand a figure back to its own sim (the lead, once it has holstered). */
export function releaseRetirementPose(agentId: string): void {
  overrides.delete(agentId);
}

/** A blank override at a spot — the ceremony fills in what it needs. */
export function poseAt(x: number, y: number, z: number, heading: number): WalkerOverride {
  return { x, y, z, heading, pitch: 0, mode: "rest", speed: 0, hidden: false, aim: 0, flash: 0 };
}
