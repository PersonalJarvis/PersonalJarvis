import { describe, expect, it } from "vitest";
import { Navigation, worldNavigation } from "./navigation";
import { rectangle } from "./spatial";
import { TileKind, buildIsland, tileToWorld, type IslandMap } from "./islandLayout";

function floor(): IslandMap {
  return { size: 256, kind: new Uint8Array(256 * 256).fill(TileKind.plaza), level: new Uint8Array(256 * 256).fill(3), blocked: new Uint8Array(256 * 256), blockedStatic: new Uint8Array(256 * 256) };
}
describe("body-aware navigation", () => {
  it("routes around a wall and validates every resulting segment", () => {
    const nav = new Navigation(floor(), [{ id: "wall", polygon: rectangle(0, 0, 2, 4) }]);
    const path = nav.route([-4, 0], [4, 0], .45);
    expect(path).not.toBeNull();
    let from: [number, number] = [-4, 0];
    for (const to of path!) { expect(nav.segment(from, to, .45)).toBe(true); from = to; }
  });
  it("rejects an enclosed goal without pretending to arrive", () => {
    const nav = new Navigation(floor(), [{ id: "house", polygon: rectangle(0, 0, 4, 4) }]);
    expect(nav.route([-5, 0], [0, 0], .5)).toBeNull();
  });
  it("rejects narrow passages for a wide figure", () => {
    const nav = new Navigation(floor(), [
      { id: "left", polygon: rectangle(-2, 0, 2, 8) },
      { id: "right", polygon: rectangle(2, 0, 2, 8) },
    ]);
    expect(nav.segment([0, -5], [0, 5], 1.1)).toBe(false);
    expect(nav.segment([0, -5], [0, 5], .5)).toBe(true);
  });
  it("reserves separate arrival positions", () => {
    const nav = new Navigation(floor(), []);
    const p = nav.nearest([0, 0], .5, [{ id: "other", x: 0, z: 0, radius: .5 }]);
    expect(p).not.toBeNull(); expect(Math.hypot(...p!)).toBeGreaterThanOrEqual(1.1);
  });
  it("connects the real square to every functional building with body clearance", () => {
    const nav = worldNavigation(), { content } = buildIsland();
    const from = nav.nearest(tileToWorld(...content.places.market.standTile), .6)!;
    for (const id of [...Object.keys(content.kitPoses), "archive"] as Array<keyof typeof content.places>) {
      const to = nav.nearest(tileToWorld(...content.places[id].standTile), .6);
      expect(to, `${id}: no safe entrance`).not.toBeNull();
      const path = nav.route(from, to!, .6);
      expect(path, `${id}: no body-safe route`).not.toBeNull();
      let p = from;
      for (const q of path!) { expect(nav.segment(p, q, .6), `${id}: unsafe segment`).toBe(true); p = q; }
    }
  }, 60000);
});
