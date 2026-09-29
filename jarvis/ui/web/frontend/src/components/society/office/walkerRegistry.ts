/**
 * Where every agent stands right now, written by the walkers each frame and
 * read by the player (who is nearby?) and the HUD (focus the camera on X).
 * A plain module map: per-frame positions must never become React state.
 */
import type { Point } from "./officeLayout";

export const agentPositions = new Map<string, Point>();

/** Agent ids the office has already seen; a newcomer arrives by the elevator. */
export const knownAgents = new Set<string>();

/** Agents sitting at their own desk right now — their monitor shows their chat. */
export const seatedAtDesk = new Set<string>();

/**
 * Agents flying along at the person's shoulder right now (Gigi). They are no
 * obstacle and no "nearby" target: they would always be the closest one.
 */
export const companions = new Set<string>();

/** Other solid bodies on the floor that are not agents (the office dog), by id. */
export const extraBodies = new Map<string, Point>();

/**
 * Every body except `selfId`: all agents, the extra bodies (the office dog) plus, optionally, the person's character.
 * Gigi counts too — it hovers at chest height, so walking "under" it would pass
 * through it — except while it follows the person as a companion.
 */
export function* bodiesExcept(selfId: string | null, player: Point | null): Generator<Point> {
  for (const [id, p] of agentPositions) if (id !== selfId && !companions.has(id)) yield p;
  for (const [id, p] of extraBodies) if (id !== selfId) yield p;
  if (player) yield player;
}
