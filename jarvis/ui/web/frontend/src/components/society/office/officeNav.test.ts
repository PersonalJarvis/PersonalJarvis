import { describe, expect, it } from "vitest";
import { allDesks, buildOfficeLayout, seatOf, standOf, type OfficeAgentInput, type OfficeLayout, type Point } from "./officeLayout";
import { buildNavGrid, findPath, hasLineOfSight, isWalkable, nearestWalkable, randomWalkablePoint, type NavGrid } from "./officeNav";
import { createRng } from "./officeBehavior";

const PROVIDERS = ["Codex", "Claude Code", "Gemini", "OpenCode", "Kimi", "GLM", "Jarvis"];

function roster(count: number): OfficeAgentInput[] {
  return Array.from({ length: count }, (_, i) => ({
    agentId: `a${i}`, name: `a${i}`, tier: i === 0 ? "lead" : "specialist",
    providerLabel: PROVIDERS[i % PROVIDERS.length], state: "idle", createdMs: i,
  }));
}

function targetsOf(layout: OfficeLayout): { name: string; p: Point }[] {
  return [
    ...allDesks(layout).flatMap((d) => [{ name: `seat ${d.id}`, p: seatOf(d) }, { name: `stand ${d.id}`, p: standOf(d) }]),
    ...layout.spots.map((s) => ({ name: `spot ${s.id}`, p: s })),
    ...layout.checkpoints.map((c) => ({ name: `checkpoint ${c.id}`, p: c.approach ?? c })),
  ];
}

/** Every sampled point of every segment is walkable, except the final snap onto a blocked target. */
function assertClearPath(grid: NavGrid, from: Point, to: Point, path: Point[], name: string): void {
  const last = path[path.length - 1];
  expect(last.x, name).toBeCloseTo(to.x, 9);
  expect(last.z, name).toBeCloseTo(to.z, 9);
  const segments = path.length - (isWalkable(grid, to) ? 0 : 1);
  let prev = from;
  for (let i = 0; i < segments; i += 1) {
    const next = path[i];
    expect(hasLineOfSight(grid, prev, next), `${name}: segment ${i}`).toBe(true);
    const len = Math.hypot(next.x - prev.x, next.z - prev.z);
    const steps = Math.max(1, Math.ceil(len / 0.03));
    for (let k = 0; k <= steps; k += 1) {
      const t = k / steps;
      const p = { x: prev.x + (next.x - prev.x) * t, z: prev.z + (next.z - prev.z) * t };
      if (!isWalkable(grid, p)) throw new Error(`${name}: segment ${i} crosses a blocked cell at ${p.x.toFixed(2)},${p.z.toFixed(2)}`);
    }
    prev = next;
  }
  if (!isWalkable(grid, to)) {
    const approach = path[path.length - 2] ?? from;
    expect(Math.hypot(approach.x - to.x, approach.z - to.z), `${name}: snap length`).toBeLessThanOrEqual(1.0 + 1e-9);
  }
}

describe("office navigation", () => {
  for (const variant of ["agents", "coding"] as const) {
    for (const count of [0, 10, 40]) {
      it(`reaches every seat, spot and checkpoint from the spawn and the elevator (${variant}, ${count} agents)`, () => {
        const layout = buildOfficeLayout(roster(count), { variant });
        const grid = buildNavGrid(layout);
        const elevator = layout.checkpoints.find((c) => c.id === "elevator")!;
        for (const start of [layout.spawn, elevator]) {
          expect(isWalkable(grid, start)).toBe(true);
          for (const { name, p } of targetsOf(layout)) {
            const path = findPath(grid, start, p);
            if (!path) throw new Error(`${name} at ${p.x.toFixed(2)},${p.z.toFixed(2)} is unreachable`);
            if (path.length === 0) continue; // already standing on it
            assertClearPath(grid, start, p, path, name);
          }
        }
      });
    }

    it(`walks back from every target to the spawn, starting inside furniture if need be (${variant})`, () => {
      const layout = buildOfficeLayout(roster(10), { variant });
      const grid = buildNavGrid(layout);
      for (const { name, p } of targetsOf(layout)) {
        const path = findPath(grid, p, layout.spawn);
        expect(path, name).not.toBeNull();
        const tail = path!.at(-1)!;
        expect(tail).toEqual(layout.spawn);
      }
    });
  }

  it("blocks obstacles and everything outside the floor", () => {
    const layout = buildOfficeLayout(roster(4));
    const grid = buildNavGrid(layout);
    const desk = allDesks(layout)[0];
    expect(isWalkable(grid, desk)).toBe(false);
    expect(isWalkable(grid, { x: layout.floor.minX - 0.5, z: 0 })).toBe(false);
    expect(isWalkable(grid, { x: 1e6, z: 1e6 })).toBe(false);
    const near = nearestWalkable(grid, desk)!;
    expect(isWalkable(grid, near)).toBe(true);
    expect(Math.hypot(near.x - desk.x, near.z - desk.z)).toBeLessThan(1.5);
    expect(nearestWalkable(grid, layout.spawn)).toEqual(layout.spawn);
  });

  it("has no line of sight through a desk row", () => {
    const layout = buildOfficeLayout(roster(4));
    const grid = buildNavGrid(layout);
    const desk = allDesks(layout).find((d) => d.id.startsWith("dept-0:0:south"))!;
    const seat = seatOf(desk);
    const across = { x: seat.x, z: desk.z + 2 };
    expect(hasLineOfSight(grid, seat, across)).toBe(false);
  });

  it("returns an empty path when already at the target, and null when unreachable", () => {
    const layout = buildOfficeLayout([]);
    const grid = buildNavGrid(layout);
    expect(findPath(grid, layout.spawn, layout.spawn)).toEqual([]);
    expect(findPath(grid, layout.spawn, { x: 1e4, z: 1e4 })).toBeNull();
  });

  it("draws random walkable points inside the requested rect", () => {
    const layout = buildOfficeLayout(roster(10));
    const grid = buildNavGrid(layout);
    const rng = createRng("wander");
    const within = layout.departments[0];
    for (let i = 0; i < 50; i += 1) {
      const p = randomWalkablePoint(grid, rng, within)!;
      expect(isWalkable(grid, p)).toBe(true);
      expect(p.x).toBeGreaterThanOrEqual(within.minX);
      expect(p.x).toBeLessThanOrEqual(within.maxX);
      expect(p.z).toBeGreaterThanOrEqual(within.minZ);
      expect(p.z).toBeLessThanOrEqual(within.maxZ);
    }
    expect(randomWalkablePoint(grid, rng, { minX: 1e5, maxX: 1e5 + 1, minZ: 0, maxZ: 1 })).toBeNull();
  });

  it("finds paths fast on a full floor", () => {
    const layout = buildOfficeLayout(roster(40));
    const grid = buildNavGrid(layout);
    const rng = createRng("perf");
    const points = Array.from({ length: 60 }, () => randomWalkablePoint(grid, rng)!);
    findPath(grid, points[0], points[1]); // warm up caches
    const started = performance.now();
    let runs = 0;
    for (let i = 0; i + 1 < points.length; i += 1) {
      expect(findPath(grid, points[i], points[i + 1])).not.toBeNull();
      runs += 1;
    }
    const perPath = (performance.now() - started) / runs;
    // Typically well under 5 ms; the bound is generous for slow CI machines.
    expect(perPath).toBeLessThan(25);
  });
});
