import { describe, expect, it } from "vitest";
import { buildOfficeLayout, seatOf, type OfficeAgentInput } from "./officeLayout";
import { buildNavGrid, findPath, isWalkable } from "./officeNav";
import { chairInReach, SEAT_REACH } from "./leadSeat";

const lead: OfficeAgentInput = { agentId: "lead", name: "Lead", tier: "lead", providerLabel: "", state: "idle", createdMs: 1 };
const staff: OfficeAgentInput = { agentId: "s1", name: "S1", tier: "specialist", providerLabel: "codex", state: "idle", createdMs: 2 };

describe("the lead's executive chair", () => {
  const layout = buildOfficeLayout([lead, staff]);
  const grid = buildNavGrid(layout);
  const desk = layout.lead.desks[0];
  const seat = seatOf(desk);

  it("is solid: nobody walks through it", () => {
    expect(isWalkable(grid, seat)).toBe(false);
  });

  it("can still be reached to sit down, ending exactly on the seat", () => {
    const path = findPath(grid, layout.spawn, seat);
    expect(path).not.toBeNull();
    const last = path![path!.length - 1];
    expect(Math.hypot(last.x - seat.x, last.z - seat.z)).toBeLessThan(0.01);
  });

  it("is within reach only from close by, and bench desks are never sittable", () => {
    expect(chairInReach(layout.lead.desks, { x: seat.x + 0.7, z: seat.z })?.id).toBe(desk.id);
    expect(chairInReach(layout.lead.desks, { x: seat.x + SEAT_REACH + 0.2, z: seat.z })).toBeNull();
    const bench = layout.departments.flatMap((d) => d.desks)[0];
    expect(chairInReach([bench], seatOf(bench))).toBeNull();
  });

  it("puts the sleeping dog in the lead office", () => {
    expect(layout.furniture.some((f) => f.kind === "dogBed" && f.room === "lead")).toBe(true);
  });
});
