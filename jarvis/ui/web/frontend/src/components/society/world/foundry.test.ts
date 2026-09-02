/**
 * The Agent Foundry's two testable halves: where a new figure walks out, and
 * WHO is owed that walk. The second one is the fragile half — the roster
 * refetches every 30 s, so an entrance that is not remembered would march the
 * same agent out of the factory over and over.
 */
import { beforeEach, describe, expect, it } from "vitest";

import {
  FOUNDRY_PORTAL_M,
  FOUNDRY_RAMP_END_M,
  LEVEL_Y,
  SUMMIT_LEVEL,
  SUMMIT_TILE,
  buildIsland,
  findPath,
  foundryWalkOut,
  isWalkable,
  kitPose,
  tileIndex,
  worldToTile,
} from "./islandLayout";
import { CAMERA_YAW_DEG } from "./worldCamera";
import { ENTRANCE_REPLAY_MS, FRESH_ENTRANCE_MS, useSpawnStore } from "./spawnStore";

const NOW = Date.UTC(2026, 8, 2, 12, 0, 0);

function resetStore(): void {
  useSpawnStore.setState({ focusRequestedMs: 0 });
  try {
    sessionStorage.clear();
  } catch {
    /* jsdom without storage: the store falls back to an empty set anyway */
  }
  useSpawnStore.setState({ portalOpenedMs: 0, announced: new Set(), seen: new Map() });
}

describe("foundryWalkOut", () => {
  it("starts inside the portal and ends past the conveyor, straight ahead", () => {
    const pose = kitPose("foundry");
    const walk = foundryWalkOut();
    const distance = (p: [number, number]) => Math.hypot(p[0] - pose.x, p[1] - pose.z);
    expect(distance(walk.from)).toBeCloseTo(FOUNDRY_PORTAL_M, 5);
    expect(distance(walk.to)).toBeCloseTo(FOUNDRY_RAMP_END_M, 5);
    // Both points sit on the building's forward axis, so the figure walks out
    // of the door rather than through a wall.
    expect(walk.heading).toBeCloseTo(pose.rotation, 5);
    const ahead = Math.atan2(walk.to[0] - walk.from[0], walk.to[1] - walk.from[1]);
    expect(ahead).toBeCloseTo(pose.rotation, 5);
  });

  it("faces its portal at the camera, which no other kit building has to", () => {
    // The whole point of the building is that you SEE an agent come out of it,
    // and the island camera never turns. A change that swings the foundry back
    // in line with the other halls hides the door — this test is the guard.
    const front = kitPose("foundry").rotation;
    const toCamera = (CAMERA_YAW_DEG * Math.PI) / 180;
    const off = Math.abs(Math.atan2(Math.sin(front - toCamera), Math.cos(front - toCamera)));
    expect(off).toBeLessThan(Math.PI / 6);
  });

  it("stands on the mountain's summit terrace", () => {
    const { map } = buildIsland();
    const [tx, tz] = worldToTile(...([kitPose("foundry").x, kitPose("foundry").z] as [number, number]));
    expect([tx, tz]).toEqual([SUMMIT_TILE[0], SUMMIT_TILE[1]]);
    expect(map.level[tileIndex(map, tx, tz)]).toBe(SUMMIT_LEVEL);
    // High above the village plateau: this is the island's backdrop, not a
    // building on the square.
    expect(LEVEL_Y[SUMMIT_LEVEL]).toBeGreaterThan(10);
  });

  it("is reachable on foot from the village square", () => {
    // The whole point of the mountain road: a new agent walks the climb down
    // into the village, and anyone can walk back up. A break anywhere in the
    // graded legs would strand the works on its own terrace.
    const { map, content } = buildIsland();
    const path = findPath(map, content.places.market.standTile, content.places.foundry.standTile);
    expect(path, "no road from the square to the foundry").not.toBeNull();
    // And the climb never asks for more than a single step at a time.
    for (let i = 1; i < (path ?? []).length; i++) {
      const [ax, az] = (path ?? [])[i - 1];
      const [bx, bz] = (path ?? [])[i];
      const rise = Math.abs(
        map.level[tileIndex(map, bx, bz)] - map.level[tileIndex(map, ax, az)],
      );
      expect(rise, `step ${i}`).toBeLessThanOrEqual(1);
    }
  });

  it("sets the figure down on ground it may stand on", () => {
    const { map } = buildIsland();
    const [tx, tz] = worldToTile(...foundryWalkOut().to);
    expect(isWalkable(map, tx, tz)).toBe(true);
  });

  it("puts the ramp foot where the walker's stand tile is", () => {
    const { content } = buildIsland();
    const [tx, tz] = worldToTile(...foundryWalkOut().to);
    const [sx, sz] = content.places.foundry.standTile;
    expect(Math.abs(tx - sx)).toBeLessThanOrEqual(1);
    expect(Math.abs(tz - sz)).toBeLessThanOrEqual(1);
  });
});

describe("spawn entrances", () => {
  beforeEach(resetStore);

  it("walks an announced agent out, and not again once it has arrived", () => {
    const { announce, claim } = useSpawnStore.getState();
    announce("scout");
    expect(claim("scout", NOW, NOW)).toBe(true);
    // The roster refetch a minute later must not repeat the parade.
    const later = NOW + ENTRANCE_REPLAY_MS + 1;
    expect(useSpawnStore.getState().claim("scout", NOW, later)).toBe(false);
  });

  it("resumes the entrance when the walker remounts mid-walk", () => {
    // A rebuilt canvas (AP-32) remounts every walker. Burning the claim on the
    // first mount would strand the newborn on the plaza mid-flare.
    const { announce, claim } = useSpawnStore.getState();
    announce("scout");
    expect(claim("scout", NOW, NOW)).toBe(true);
    expect(useSpawnStore.getState().claim("scout", NOW, NOW + 900)).toBe(true);
  });

  it("never grants an entrance a second time to an agent that was denied one", () => {
    const { claim } = useSpawnStore.getState();
    expect(claim("veteran", NOW - FRESH_ENTRANCE_MS - 1, NOW)).toBe(false);
    expect(useSpawnStore.getState().claim("veteran", NOW - 10, NOW + 10)).toBe(false);
  });

  it("walks out a row created in another window while it is still fresh", () => {
    const { claim } = useSpawnStore.getState();
    expect(claim("fresh", NOW - 5_000, NOW)).toBe(true);
  });

  it("leaves an agent created long ago standing where it is", () => {
    const { claim } = useSpawnStore.getState();
    expect(claim("veteran", NOW - FRESH_ENTRANCE_MS - 1, NOW)).toBe(false);
    expect(useSpawnStore.getState().portalOpenedMs).toBe(0);
  });

  it("asks the island to look at the foundry when the agent is made here", () => {
    // A row created in this window is one the viewer chose to make: the stage
    // swings to the works so they see it happen. A row that merely turned up
    // fresh from somewhere else does not move anyone's camera.
    useSpawnStore.getState().announce("scout");
    expect(useSpawnStore.getState().focusRequestedMs).toBeGreaterThan(0);
    useSpawnStore.getState().clearFocus();
    expect(useSpawnStore.getState().focusRequestedMs).toBe(0);
    useSpawnStore.getState().claim("stranger", NOW, NOW);
    expect(useSpawnStore.getState().focusRequestedMs).toBe(0);
  });

  it("frames the whole conveyor when it looks at the foundry", () => {
    // The camera aims at the middle of the walk, so the portal the figure
    // steps out of and the ramp foot it lands on are both in shot.
    const walk = foundryWalkOut();
    const mid: [number, number] = [
      (walk.from[0] + walk.to[0]) / 2,
      (walk.from[1] + walk.to[1]) / 2,
    ];
    const pose = kitPose("foundry");
    const ahead = Math.hypot(mid[0] - pose.x, mid[1] - pose.z);
    expect(ahead).toBeGreaterThan(FOUNDRY_PORTAL_M);
    expect(ahead).toBeLessThan(FOUNDRY_RAMP_END_M);
  });

  it("opens the portal for the building to react to", () => {
    useSpawnStore.getState().claim("scout", NOW, NOW);
    expect(useSpawnStore.getState().portalOpenedMs).toBe(NOW);
  });

  it("remembers walked agents across a reload of the same window", () => {
    useSpawnStore.getState().claim("scout", NOW, NOW);
    // A reload rebuilds the store from sessionStorage; simulate that.
    const stored = sessionStorage.getItem("jarvis.world.foundry.seen.v2");
    expect(stored).toContain("scout");
    useSpawnStore.setState({
      portalOpenedMs: 0,
      announced: new Set(),
      seen: new Map(JSON.parse(stored ?? "[]") as Array<[string, number]>),
    });
    // Long after the walk: the reload shows the agent where it stands.
    const later = NOW + ENTRANCE_REPLAY_MS + 1;
    expect(useSpawnStore.getState().claim("scout", NOW, later)).toBe(false);
  });
});
