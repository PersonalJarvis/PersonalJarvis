/**
 * The building has two floors joined by an elevator: the Jarvis agents office
 * and, one ride up, the coding floor with a figure per IDE coding session.
 * Pure helpers for what differs per floor; the stage wires them.
 */
import type { OfficeLayout } from "./officeLayout";
import type { OfficeFloor } from "./officeStore";
import { knownAgents } from "./walkerRegistry";

export interface FloorPose { x: number; z: number; heading: number }

/**
 * Where the character steps out on arrival: at the elevator checkpoint,
 * facing away from the doors — or where it last stood on that floor.
 * Falls back to the spawn point when the plan has no elevator.
 */
export function arrivalPose(layout: OfficeLayout, at: "elevator" | "remembered", remembered?: FloorPose): FloorPose {
  if (at === "remembered" && remembered) return remembered;
  const cp = layout.checkpoints.find((c) => c.id === "elevator");
  if (!cp) return { x: layout.spawn.x, z: layout.spawn.z, heading: Math.PI };
  const doors = layout.furniture.find((f) => f.kind === "elevator");
  const heading = doors && Math.hypot(cp.x - doors.x, cp.z - doors.z) > 1e-3
    ? Math.atan2(cp.x - doors.x, cp.z - doors.z)
    : Math.PI;
  return { x: cp.x, z: cp.z, heading };
}

/** Agent ids each floor has already seen; a newcomer on that floor arrives by the elevator. */
const codingKnown = new Set<string>();
export function knownOnFloor(floor: OfficeFloor): Set<string> {
  return floor === "agents" ? knownAgents : codingKnown;
}

/**
 * Newcomers among `ids` on a floor, updating what the floor knows. The first
 * sight of a floor knows everyone (nobody "arrives" just because you came up);
 * ids that left are forgotten so the set never outgrows the roster.
 */
export function noteArrivals(known: Set<string>, ids: readonly string[]): string[] {
  if (ids.length === 0) return [];
  if (known.size === 0) { ids.forEach((id) => known.add(id)); return []; }
  const present = new Set(ids);
  for (const id of [...known]) if (!present.has(id)) known.delete(id);
  const fresh = ids.filter((id) => !known.has(id));
  fresh.forEach((id) => known.add(id));
  return fresh;
}
