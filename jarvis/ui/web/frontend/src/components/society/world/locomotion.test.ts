import { describe, expect, it } from "vitest";
import { MotionWorld } from "./locomotion";
import { Navigation } from "./navigation";
import { TileKind } from "./islandLayout";
import { rectangle } from "./spatial";

function simulation(wall = false) {
  const n = 256 * 256;
  const nav = new Navigation({ size: 256, kind: new Uint8Array(n).fill(TileKind.plaza), level: new Uint8Array(n).fill(3),
    blocked: new Uint8Array(n), blockedStatic: new Uint8Array(n) }, wall ? [{ id: "wall", polygon: rectangle(0, 0, 2, 6) }] : []);
  return new MotionWorld(nav, () => [7, 7]);
}
describe("fixed-step locomotion", () => {
  it("discards a late worker result after the destination changes", async () => {
    const world = simulation();
    let complete: ((path: Array<[number, number]> | null) => void) | undefined;
    world.planner = () => new Promise(resolve => { complete = resolve; });
    const a = world.add("one", [-7, 0], .5, "idle", [7, 0])!;
    world.advance(1 / 30);
    expect(a.planning).toBe(true);
    world.target(a.id, [-7, 5]);
    complete!([[7, 0]]);
    await Promise.resolve();
    expect(a.path).toEqual([]);
    expect(a.desired).toEqual([-7, 5]);
    expect(a.arrived).toBe(false);
  });
  it("produces the same movement at 30 and 60 render frames per second", () => {
    const a = simulation(), b = simulation();
    a.add("one", [-7, 0], .5, "idle", [7, 0]); b.add("one", [-7, 0], .5, "idle", [7, 0]);
    for (let i = 0; i < 180; i++) a.advance(1 / 30);
    for (let i = 0; i < 360; i++) b.advance(1 / 60);
    expect(a.actors.get("one")!.x).toBeCloseTo(b.actors.get("one")!.x, 8);
  });
  it("never crosses a building while walking around it", () => {
    const world = simulation(true), a = world.add("one", [-5, 0], .5, "working", [5, 0])!;
    for (let i = 0; i < 700; i++) { const before: [number, number] = [a.x, a.z]; world.advance(1 / 30); expect(world.nav.segment(before, [a.x, a.z], a.radius)).toBe(true); }
    expect(a.arrived).toBe(true); expect(a.mode).toBe("work");
  });
  it("caps stalled frames and never teleports to catch up", () => {
    const world = simulation(), a = world.add("one", [-7, 0], .5, "idle", [7, 0])!;
    expect(world.advance(30)).toBe(5); expect(a.x).toBeLessThan(-6.7);
  });
  it("keeps two approaching bodies separated on every tick", () => {
    const world = simulation(), a = world.add("a", [-4, 0], .5, "idle", [4, 0])!, b = world.add("b", [4, 0], .5, "idle", [-4, 0])!;
    for (let i = 0; i < 450; i++) { world.advance(1 / 30); expect(Math.hypot(a.x - b.x, a.z - b.z)).toBeGreaterThanOrEqual(1.059); }
  });
});
