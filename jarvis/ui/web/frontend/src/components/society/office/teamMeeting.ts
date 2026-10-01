/**
 * Team meetings in the team room: which chair each member takes, and how far
 * the meeting has got (who already sits at the table, who is still on the way,
 * who keeps working at their desk). Pure, so the panel and tests share it.
 */
import type { SocietyAgent } from "../data";
import type { OfficeLayout, Point } from "./officeLayout";
import type { Summon, TeamMeeting } from "./officeStore";

/** A meeting holds its members at the table this long unless it is ended earlier. */
export const MEETING_MS = 10 * 60_000;

/** How close to its chair a member must be to count as seated, in metres. */
const SEATED_WITHIN_M = 0.35;
/** The same for a member without a chair, who stops about a step (1.2 m) from its point. */
const STANDING_WITHIN_M = 1.6;

export interface MeetingSeat { target: Point; spotId?: string }

/**
 * A chair for every member, middle chairs first and facing pairs together, so
 * a small team sits across from each other. Members beyond the chairs stand at
 * the table's short ends.
 */
export function meetingSeats(layout: OfficeLayout, members: readonly string[]): Record<string, MeetingSeat> {
  const table = layout.furniture.find((f) => f.kind === "meetingTable");
  const centre = table ? { x: table.x, z: table.z } : layout.spawn;
  const chairs = layout.spots.filter((s) => s.kind === "meeting")
    .sort((a, b) => Math.abs(a.x - centre.x) - Math.abs(b.x - centre.x) || a.x - b.x || a.z - b.z);
  const seats: Record<string, MeetingSeat> = {};
  members.forEach((id, i) => {
    const chair = chairs[i];
    if (chair) { seats[id] = { target: { x: chair.x, z: chair.z }, spotId: chair.id }; return; }
    const extra = i - chairs.length;
    const side = extra % 2 === 0 ? 1 : -1;
    seats[id] = { target: { x: centre.x + side * (2.4 + 0.5 * Math.floor(extra / 2)), z: centre.z } };
  });
  return seats;
}

export interface MeetingProgress {
  /** Members sitting (or standing) at their place at the table. */
  seated: string[];
  /** Members on their way to the table. */
  arriving: string[];
  /** Members that keep working at their desk: a meeting never interrupts real work. */
  busy: string[];
  /** Members no longer in the office (removed or retired agents). */
  missing: string[];
}

export function meetingProgress(
  meeting: TeamMeeting, agents: readonly SocietyAgent[], summons: Record<string, Summon>, positions: ReadonlyMap<string, Point>,
): MeetingProgress {
  const progress: MeetingProgress = { seated: [], arriving: [], busy: [], missing: [] };
  for (const id of meeting.members) {
    const agent = agents.find((a) => a.agentId === id);
    if (!agent) { progress.missing.push(id); continue; }
    if (agent.state === "working") { progress.busy.push(id); continue; }
    const where = positions.get(id);
    const summon = summons[id];
    // A chair is exact; a member standing at the table's end stops a step short of its point.
    const within = summon?.spotId ? SEATED_WITHIN_M : STANDING_WITHIN_M;
    if (where && summon && Math.hypot(where.x - summon.target.x, where.z - summon.target.z) <= within) progress.seated.push(id);
    else progress.arriving.push(id);
  }
  return progress;
}

/** Minutes left in a meeting, rounded up; 0 once it is over. */
export function minutesLeft(meeting: TeamMeeting, nowMs: number): number {
  return Math.max(0, Math.ceil((meeting.untilMs - nowMs) / 60_000));
}
