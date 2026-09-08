import { beforeEach, describe, expect, it } from "vitest";

import {
  SNAP_STEP_RAD,
  normalizeOverrides,
  settleYaw,
  syncBuildingPoses,
  useBuildingPoses,
} from "./buildingPoses";
import {
  buildIsland,
  defaultBuildingYaw,
  houseId,
  isWalkable,
  kitId,
  normalizeAngle,
  resetIslandCache,
  worldToTile,
} from "./islandLayout";

const KEY = "jarvis.world.poses.v1";

describe("buildingPoses", () => {
  beforeEach(() => {
    localStorage.removeItem(KEY);
    resetIslandCache();
    useBuildingPoses.getState().resetAll();
    useBuildingPoses.setState({ selected: null, rotating: false });
  });

  it("keeps only well-formed overrides from storage", () => {
    expect(normalizeOverrides(null)).toEqual({});
    expect(normalizeOverrides({ "house:5": 1.2, "kit:cli": -0.4, "tree:3": 1, "house:x": 1, "kit:mcp": "no" })).toEqual({
      "house:5": 1.2,
      "kit:cli": -0.4,
    });
    // Angles come back wrapped into (−π, π].
    expect(normalizeOverrides({ "house:6": 4 })["house:6"]).toBeCloseTo(4 - 2 * Math.PI, 9);
  });

  it("snaps a drag to the designed heading when close, and to 15° steps with Shift", () => {
    const id = houseId(6);
    const rest = defaultBuildingYaw(id);
    expect(settleYaw(id, rest + 0.03, false)).toBe(rest);
    expect(settleYaw(id, rest + 0.5, false)).toBeCloseTo(rest + 0.5, 9);
    const stepped = settleYaw(id, 0.3, true);
    expect(stepped / SNAP_STEP_RAD).toBeCloseTo(Math.round(stepped / SNAP_STEP_RAD), 9);
  });

  it("turns a house in the island itself, so walkers route around where it now stands", () => {
    const island = buildIsland();
    const house = island.content.houses[0];
    const id = houseId(house.slot);
    const rest = house.rotation;
    // Turn the house a quarter round: its long side now points where its short side did.
    const before = island.map.blocked.slice();
    const store = useBuildingPoses.getState();
    store.setYaw(id, rest + Math.PI / 2);
    // Headings are stored wrapped into (−π, π], so a quarter turn off a
    // corner house's 135° comes back as the same heading with the other sign.
    expect(house.rotation).toBeCloseTo(normalizeAngle(rest + Math.PI / 2), 6);
    // The footprint moved with it: the blocked layer changed, the centre stays covered.
    expect(island.map.blocked).not.toEqual(before);
    expect(isWalkable(island.map, ...worldToTile(house.x, house.z))).toBe(false);
    expect(useBuildingPoses.getState().generation).toBeGreaterThan(0);
    // Reset restores the designed heading and the exact footprint.
    useBuildingPoses.getState().resetYaw(id);
    expect(house.rotation).toBe(rest);
    expect(island.map.blocked).toEqual(before);
    expect(useBuildingPoses.getState().yaw).toEqual({});
  });

  it("moves a ring hub's stand tile with the hub and keeps it walkable", () => {
    const island = buildIsland();
    const before = [...island.content.places.cli.standTile] as [number, number];
    useBuildingPoses.getState().setYaw(kitId("cli"), defaultBuildingYaw(kitId("cli")) + Math.PI);
    const after = island.content.places.cli.standTile;
    expect(after).not.toEqual(before);
    expect(isWalkable(island.map, after[0], after[1])).toBe(true);
    expect(island.content.places.cli.facing).toBeCloseTo(defaultBuildingYaw(kitId("cli")), 6);
    useBuildingPoses.getState().resetAll();
    expect(island.content.places.cli.standTile).toEqual(before);
  });

  it("persists per viewer and comes back through sync", () => {
    const id = houseId(7);
    const yaw = normalizeAngle(defaultBuildingYaw(id) + Math.PI);
    useBuildingPoses.getState().setYaw(id, yaw);
    expect(JSON.parse(localStorage.getItem(KEY) ?? "{}")).toEqual({ yaw: { [id]: yaw } });
    resetIslandCache();
    const fresh = buildIsland();
    const house = fresh.content.houses.find((h) => h.slot === 7)!;
    expect(house.rotation).toBe(house.defaultRotation);
    syncBuildingPoses();
    expect(house.rotation).toBeCloseTo(yaw, 9);
  });
});
