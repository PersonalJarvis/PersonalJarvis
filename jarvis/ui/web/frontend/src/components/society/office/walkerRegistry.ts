/**
 * Where every agent stands right now, written by the walkers each frame and
 * read by the player (who is nearby?) and the HUD (focus the camera on X).
 * A plain module map: per-frame positions must never become React state.
 */
import type { Point } from "./officeLayout";

export const agentPositions = new Map<string, Point>();

/** Agent ids the office has already seen; a newcomer arrives by the elevator. */
export const knownAgents = new Set<string>();
