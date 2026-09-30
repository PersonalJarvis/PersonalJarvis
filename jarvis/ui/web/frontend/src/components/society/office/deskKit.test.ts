import { describe, expect, it } from "vitest";
import { dressDesk, dressOfficeDesk, kitPlacements, oakPlanterPlacements, officeKitPlacements, planterPlacements } from "./deskKit";
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

  it.each(["coding", "agents"] as const)("stands the %s floor's bench planters clear of every desk and chair", (variant) => {
    const layout = buildOfficeLayout(roster(20), { variant });
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

describe("agents floor office kit", () => {
  it("dresses a desk the same way every time, from its id alone", () => {
    for (const id of ["dept-0:0:north:0", "dept-3:1:south:2", "y"]) {
      expect(officeKitPlacements(dressOfficeDesk(id, true))).toEqual(officeKitPlacements(dressOfficeDesk(id, true)));
    }
  });

  it("varies from desk to desk", () => {
    const kits = Array.from({ length: 32 }, (_, i) => dressOfficeDesk(`dept-1:${i >> 3}:south:${i & 7}`, true));
    expect(new Set(kits.map((k) => k.corner)).size).toBeGreaterThan(2);
    expect(new Set(kits.map((k) => k.side)).size).toBeGreaterThan(2);
    expect(new Set(kits.map((k) => k.front)).size).toBeGreaterThan(1);
  });

  it("keeps an empty desk's tray, plant and lamp when an agent sits down", () => {
    for (let i = 0; i < 40; i += 1) {
      const id = `dept-2:0:north:${i}`;
      const empty = dressOfficeDesk(id, false), taken = dressOfficeDesk(id, true);
      expect(empty.front).toBeNull();
      expect(empty.photo || empty.phone || empty.cup || empty.pinned).toBe(false);
      if (empty.corner !== null) expect(taken.corner).toBe(empty.corner);
      if (empty.side !== null) expect(taken.side).toBe(empty.side);
      if (empty.penCup) expect(taken.penCup).toBe(true);
      expect(taken.phone && taken.side === "dock").toBe(false);
    }
  });

  it("never reaches into the monitor, and keeps clear of the desk's mug", () => {
    for (let i = 0; i < 80; i += 1) {
      for (const part of officeKitPlacements(dressOfficeDesk(`o:${i}`, true))) {
        const [x, y, z] = part.p;
        if (Math.abs(x) < 0.4 && z > -0.3 && z < 0.2) expect(y, part.part).toBeLessThan(0.96);
        // The screen itself: nothing above the desk inside its width, in front of the felt panel.
        if (Math.abs(x) < 0.37 && z > -0.39) expect(y, part.part).toBeLessThan(0.96);
        // DeskInstances' mug at (-0.52, 0.02).
        expect(Math.hypot(x + 0.52, z - 0.02), part.part).toBeGreaterThan(0.07);
        // Everything stays on the desk top.
        expect(Math.abs(x), part.part).toBeLessThan(0.75);
        expect(Math.abs(z), part.part).toBeLessThan(0.4);
      }
    }
  });

  it("seeds each oak planter by its key and keeps its plants over the box", () => {
    const rect = { minX: 0, maxX: 0.36, minZ: 0, maxZ: 1.56 };
    expect(oakPlanterPlacements(rect, "k")).toEqual(oakPlanterPlacements(rect, "k"));
    expect(oakPlanterPlacements(rect, "k")).not.toEqual(oakPlanterPlacements(rect, "other"));
    for (const part of oakPlanterPlacements(rect, "k")) {
      expect(part.p[0]).toBeGreaterThan(rect.minX - 0.15);
      expect(part.p[0]).toBeLessThan(rect.maxX + 0.15);
      expect(part.p[2]).toBeGreaterThan(rect.minZ - 0.15);
      expect(part.p[2]).toBeLessThan(rect.maxZ + 0.15);
    }
  });
});
