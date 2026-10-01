import { describe, expect, it } from "vitest";
import { buildOfficeLayout, seatOf, type OfficeAgentInput } from "./officeLayout";
import { buildNavGrid, findPath, isWalkable } from "./officeNav";
import { chairInReach, SEAT_REACH, seatDesks } from "./leadSeat";

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

describe("Mission Control's boss chair", () => {
  const layout = buildOfficeLayout([staff], { variant: "coding" });
  const grid = buildNavGrid(layout);
  const desk = layout.command!;
  const seat = seatOf(desk);

  it("is a seat on the coding floor only, facing the monitors (south)", () => {
    expect(desk.id).toBe("command");
    expect(buildOfficeLayout([lead, staff]).command).toBeUndefined();
    expect(seatDesks(layout)).toEqual([desk]);
    // The person sits north of the desk, between it and the wall, looking south at the monitors.
    expect(seat.z).toBeLessThan(desk.z);
    expect(seat.facing).toBe(0);
  });

  it("is solid, yet reachable to sit down, and within reach only from close by", () => {
    expect(isWalkable(grid, seat)).toBe(false);
    const path = findPath(grid, layout.spawn, seat);
    expect(path).not.toBeNull();
    expect(chairInReach(seatDesks(layout), { x: seat.x + 0.7, z: seat.z })?.id).toBe("command");
    expect(chairInReach(seatDesks(layout), { x: seat.x + SEAT_REACH + 0.2, z: seat.z })).toBeNull();
  });
});
