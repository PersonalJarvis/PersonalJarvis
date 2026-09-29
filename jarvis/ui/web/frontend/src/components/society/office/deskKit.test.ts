import { describe, expect, it } from "vitest";
import { dressDesk, kitPlacements, planterPlacements } from "./deskKit";
import { allDesks, benchPlanters, buildOfficeLayout, chairRect, deskRect, seatOf, type Rect } from "./officeLayout";

const overlaps = (a: Rect, b: Rect) => a.minX < b.maxX && b.minX < a.maxX && a.minZ < b.maxZ && b.minZ < a.maxZ;

const roster = (n: number) => Array.from({ length: n }, (_, i) => ({
  agentId: `a${i}`, name: `A${i}`, tier: "specialist" as const, providerLabel: `ws${i % 3}`, state: "working" as const, createdMs: i,
}));

describe("desk dressing", () => {
  it("dresses a desk the same way every time, from its id alone", () => {
    for (const id of ["dept-0:0:north:0", "dept-2:1:south:3", "x"]) {
      expect(dressDesk(id, true)).toEqual(dressDesk(id, true));
      expect(kitPlacements(dressDesk(id, false))).toEqual(kitPlacements(dressDesk(id, false)));
    }
  });

  it("varies from desk to desk", () => {
    const kits = Array.from({ length: 32 }, (_, i) => dressDesk(`dept-0:${i >> 3}:north:${i & 7}`, true));
    expect(new Set(kits.map((k) => k.mat)).size).toBeGreaterThan(2);
    expect(new Set(kits.map((k) => k.side)).size).toBeGreaterThan(1);
    expect(new Set(kits.map((k) => k.corner)).size).toBeGreaterThan(2);
  });

  it("keeps a desk's mat, plant and lamp when an agent sits down; only personal things are added", () => {
    for (let i = 0; i < 40; i += 1) {
      const id = `dept-1:0:south:${i}`;
      const empty = dressDesk(id, false), taken = dressDesk(id, true);
      expect(empty.mat).toBe(taken.mat);
      expect(empty.notebook).toBeNull();
      expect(empty.headphones).toBeNull();
      expect(empty.notes).toBe(0);
      expect(empty.side === "laptop").toBe(false);
      if (empty.corner === "succulent") expect(taken.corner).toBe("succulent");
      if (empty.side === "lamp") expect(taken.side).toBe("lamp");
    }
  });

  it("never reaches into the monitor, so the live terminal screen stays clear", () => {
    for (let i = 0; i < 60; i += 1) {
      for (const part of kitPlacements(dressDesk(`d:${i}`, true))) {
        const [x, y, z] = part.p;
        // The screen: |x| ≤ 0.36, 0.965–1.395 m high, at z ≈ -0.21; nothing in front of it above the desk.
        if (Math.abs(x) < 0.4 && z > -0.3 && z < 0.2) expect(y, part.part).toBeLessThan(0.96);
      }
    }
  });

  it("seeds each planter by its key", () => {
    const rect = { minX: 0, maxX: 0.36, minZ: 0, maxZ: 1.56 };
    expect(planterPlacements(rect, "k")).toEqual(planterPlacements(rect, "k"));
    expect(planterPlacements(rect, "k")).not.toEqual(planterPlacements(rect, "other"));
  });

  it("stands the bench planters clear of every desk and chair", () => {
    const layout = buildOfficeLayout(roster(20), { variant: "coding" });
    const desks = allDesks(layout);
    const planters = layout.departments.flatMap(benchPlanters);
    expect(planters.length).toBe(layout.departments.reduce((n, d) => n + new Set(d.desks.map((k) => k.id.split(":").at(-3))).size * 2, 0));
    for (const { rect } of planters) {
      for (const desk of desks) {
        expect(overlaps(rect, deskRect(desk))).toBe(false);
        expect(overlaps(rect, chairRect(seatOf(desk)))).toBe(false);
      }
      expect(layout.obstacles).toContainEqual(rect);
    }
  });
});
