import { describe, expect, it } from "vitest";
import type { SocietyAgent } from "../data";
import { allDesks, buildOfficeLayout, type OfficeAgentInput } from "./officeLayout";
import { buildNavGrid, isWalkable } from "./officeNav";
import { createRng, planFor, SpotBook, type PlanInput } from "./officeBehavior";
import { MEETING_MS, meetingProgress, meetingSeats, minutesLeft } from "./teamMeeting";
import type { TeamMeeting } from "./officeStore";

const roster: OfficeAgentInput[] = Array.from({ length: 9 }, (_, i) => ({
  agentId: `a${i}`, name: `a${i}`, tier: "specialist", providerLabel: "Codex", state: "idle", createdMs: i,
}));
const layout = buildOfficeLayout(roster);
const grid = buildNavGrid(layout);
const ids = roster.map((r) => r.agentId);

function agent(agentId: string, state: SocietyAgent["state"]): SocietyAgent {
  return { agentId, name: agentId, state } as SocietyAgent;
}

function input(agentId: string, book: SpotBook, extra: Partial<PlanInput>): PlanInput {
  return {
    agentId, state: "idle", desk: allDesks(layout).find((d) => d.agentId === agentId) ?? null, layout, grid,
    rng: createRng(agentId), book, previous: null, workingColleagues: [], calledTo: null, ...extra,
  };
}

describe("meetingSeats", () => {
  it("gives every member its own chair, middle chairs first, facing pairs together", () => {
    const seats = meetingSeats(layout, ["a0", "a1"]);
    const [s0, s1] = [seats.a0, seats.a1];
    expect(s0.spotId).toMatch(/^meeting-/);
    expect(s1.spotId).toMatch(/^meeting-/);
    expect(s0.spotId).not.toBe(s1.spotId);
    // Across the table from each other: same x, opposite sides.
    expect(s0.target.x).toBeCloseTo(s1.target.x);
    expect(s0.target.z).not.toBeCloseTo(s1.target.z);
  });

  it("lets members beyond the chairs stand at the table's ends on walkable floor", () => {
    const seats = meetingSeats(layout, ids);
    const chairs = layout.spots.filter((s) => s.kind === "meeting").length;
    const standing = ids.filter((id) => !seats[id].spotId);
    expect(standing).toHaveLength(ids.length - chairs);
    for (const id of standing) expect(isWalkable(grid, seats[id].target)).toBe(true);
  });
});

describe("planFor with a meeting chair", () => {
  it("sits a called member on its chair", () => {
    const seat = meetingSeats(layout, ["a3"]).a3;
    const plan = planFor(input("a3", new SpotBook(), { calledTo: seat.target, calledSeat: seat.spotId }));
    expect(plan.kind).toBe("meeting");
    expect(plan.pose).toBe("sit");
    expect(plan.spotId).toBe(seat.spotId);
    expect(plan.target).toEqual(seat.target);
  });

  it("takes another free chair when its own is occupied", () => {
    const book = new SpotBook();
    const seat = meetingSeats(layout, ["a3"]).a3;
    book.claim(seat.spotId!, "stranger");
    const plan = planFor(input("a3", book, { calledTo: seat.target, calledSeat: seat.spotId }));
    expect(plan.kind).toBe("meeting");
    expect(plan.spotId).not.toBe(seat.spotId);
    expect(plan.spotId).toMatch(/^meeting-/);
  });

  it("never pulls a working agent away from its screen", () => {
    const seat = meetingSeats(layout, ["a3"]).a3;
    expect(planFor(input("a3", new SpotBook(), { state: "working", calledTo: seat.target, calledSeat: seat.spotId })).kind).toBe("work");
  });
});

describe("meetingProgress", () => {
  it("sorts members into seated, arriving, busy and missing", () => {
    const meeting: TeamMeeting = { groupId: "g", name: "Crew", members: ["a0", "a1", "a2", "gone"], untilMs: 1 };
    const seats = meetingSeats(layout, meeting.members);
    const summons = Object.fromEntries(Object.entries(seats).map(([id, s]) => [id, { ...s, untilMs: 1 }]));
    const positions = new Map([
      ["a0", seats.a0.target],
      ["a1", { x: seats.a1.target.x + 3, z: seats.a1.target.z }],
      ["a2", { x: 0, z: 0 }],
    ]);
    const progress = meetingProgress(meeting, [agent("a0", "idle"), agent("a1", "idle"), agent("a2", "working")], summons, positions);
    expect(progress).toEqual({ seated: ["a0"], arriving: ["a1"], busy: ["a2"], missing: ["gone"] });
  });

  it("counts minutes left, rounded up, and none after the end", () => {
    const meeting: TeamMeeting = { groupId: "g", name: "Crew", members: [], untilMs: 1_000 + MEETING_MS };
    expect(minutesLeft(meeting, 1_000)).toBe(10);
    expect(minutesLeft(meeting, 1_000 + MEETING_MS - 30_000)).toBe(1);
    expect(minutesLeft(meeting, 2_000 + MEETING_MS)).toBe(0);
  });
});
