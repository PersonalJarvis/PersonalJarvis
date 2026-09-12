import { describe, expect, it } from "vitest";
import { advancePlayer, createPlayer, isPositionClear, RUN_SPEED, sweepSlide, WALK_SPEED, type CollisionWorld, type PlayerInput } from "./controller";
import { box, ROADS, surfaceHeight } from "./world";

const flat: CollisionWorld = { colliders: [], ground: () => 0 };
const walk: PlayerInput = { forward: 1, right: 0, run: false, jump: false };
const still: PlayerInput = { forward: 0, right: 0, run: false, jump: false };

describe("bounded Mars movement", () => {
  it.each([30, 60, 120])("moves consistently at %s render FPS", (fps) => {
    const player = createPlayer([0, 0, 0]);
    for (let i = 0; i < fps; i++) advancePlayer(player, walk, 0, 1 / fps, flat);
    expect(player.position[2]).toBeCloseTo(-WALK_SPEED, 3);
    expect(player.position[1]).toBe(0);
  });
  it("normalizes diagonals and caps a thirty-second frame stall", () => {
    const player = createPlayer([0, 0, 0]);
    expect(advancePlayer(player, { ...walk, right: 1, run: true }, 0, 30, flat)).toBe(12);
    expect(Math.hypot(player.position[0], player.position[2])).toBeCloseTo(RUN_SPEED * 0.1, 3);
    const before = [...player.position];
    advancePlayer(player, walk, 0, -1, flat); advancePlayer(player, walk, 0, NaN, flat);
    expect(player.position).toEqual(before);
  });
  it("sweeps through a thin-wall fixture without tunneling and slides along it", () => {
    const wall: CollisionWorld = { ground: () => 0, colliders: [box("thin-wall", 3, 0, 0, 0.1, 5, 10)] };
    const next = sweepSlide([0, 0, 0], 20, 2, wall);
    expect(next[0]).toBeLessThan(2.56); expect(next[2]).toBeGreaterThan(1.9);
    expect(isPositionClear(next, wall)).toBe(true);
  });
  it("enters through the operations door and stops at the back wall", () => {
    const player = createPlayer([294, surfaceHeight(294, 75), 75]);
    for (let i = 0; i < 240; i++) advancePlayer(player, walk, 0, 1 / 60);
    expect(player.position[2]).toBeLessThan(64);
    expect(player.position[2]).toBeGreaterThan(57);
    expect(isPositionClear(player.position)).toBe(true);
  });
  it("crosses the Outpost bridge without losing deck height at its endpoints", () => {
    const road = ROADS.find((item) => item.id === "route-01")!;
    const player = createPlayer([road.start[0], surfaceHeight(road.start[0], road.start[2]), road.start[2]]);
    const dx = road.end[0] - road.start[0], dz = road.end[2] - road.start[2];
    const yaw = -Math.atan2(dz, dx);
    const frames = Math.ceil(Math.hypot(dx, dz) / WALK_SPEED * 60);
    for (let i = 0; i < frames; i++) advancePlayer(player, { ...still, right: 1 }, yaw, 1 / 60);
    expect(player.position[0]).toBeCloseTo(road.end[0], 0);
    expect(player.position[1]).toBeCloseTo(58.08, 1);
  });
  it("lands after a single jump and does not auto-jump while Space stays held", () => {
    const player = createPlayer([0, 0, 0]);
    let peak = 0;
    for (let i = 0; i < 180; i++) {
      advancePlayer(player, { ...still, jump: true }, 0, 1 / 60, flat);
      peak = Math.max(peak, player.position[1]);
    }
    expect(peak).toBeGreaterThan(0.7); expect(peak).toBeLessThan(1);
    expect(player.position[1]).toBe(0); expect(player.grounded).toBe(true);
  });
  it("stops upward motion at a low ceiling", () => {
    const world = { ground: () => 0, colliders: [box("ceiling", 0, 2.1, 0, 10, 0.2, 10)] };
    const player = createPlayer([0, 0, 0]);
    for (let i = 0; i < 60; i++) {
      advancePlayer(player, { ...still, jump: i < 2 }, 0, 1 / 60, world);
      expect(player.position[1] + 1.8).toBeLessThanOrEqual(2.1);
    }
    expect(player.grounded).toBe(true);
  });
  it("climbs a small step but refuses an unsupported cliff edge", () => {
    const step = { colliders: [], ground: (_x: number, z: number) => z < -1 ? 0.2 : 0 };
    const player = createPlayer([0, 0, 0]);
    for (let i = 0; i < 60; i++) advancePlayer(player, walk, 0, 1 / 60, step);
    expect(player.position[2]).toBeLessThan(-3); expect(player.position[1]).toBeCloseTo(0.2);
    const cliff = { colliders: [], ground: (_x: number, z: number) => z < -1 ? -20 : 0 };
    const edge = createPlayer([0, 0, 0]);
    for (let i = 0; i < 60; i++) advancePlayer(edge, walk, 0, 1 / 60, cliff);
    expect(edge.position[2]).toBeGreaterThan(-1); expect(edge.position[1]).toBe(0);
  });
});
