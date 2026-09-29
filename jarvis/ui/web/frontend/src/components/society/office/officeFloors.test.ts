import { afterEach, describe, expect, it } from "vitest";
import { buildOfficeLayout, type OfficeAgentInput } from "./officeLayout";
import { buildNavGrid, isWalkable } from "./officeNav";
import { arrivalPose, knownOnFloor, noteArrivals } from "./officeFloors";
import { officeSession, otherFloor, player, switchFloor, useOfficeStore } from "./officeStore";

const AGENTS: OfficeAgentInput[] = [
  { agentId: "a1", name: "One", tier: "specialist", providerLabel: "Codex", state: "working", createdMs: 1 },
  { agentId: "a2", name: "Two", tier: "specialist", providerLabel: "Claude", state: "idle", createdMs: 2 },
];

function resetBuilding() {
  useOfficeStore.setState({ floor: "agents", selection: null, nearby: null, summons: {}, walkTo: null, teamDraft: [] });
  officeSession.playerPlaced = false;
  officeSession.floors = {};
  officeSession.arrival = null;
  Object.assign(player, { x: 0, z: 0, heading: Math.PI, path: [], moving: false });
}

afterEach(resetBuilding);

describe("floors", () => {
  it("names the other floor", () => {
    expect(otherFloor("agents")).toBe("coding");
    expect(otherFloor("coding")).toBe("agents");
  });

  it("steps out of the elevator onto walkable floor, facing away from the doors, on both floors", () => {
    for (const variant of ["agents", "coding"] as const) {
      const layout = buildOfficeLayout(AGENTS, { variant });
      const stop = layout.checkpoints.find((cp) => cp.id === "elevator");
      expect(stop, variant).toBeDefined();
      const pose = arrivalPose(layout, "elevator");
      expect(pose.x).toBeCloseTo(stop!.x);
      expect(pose.z).toBeCloseTo(stop!.z);
      expect(isWalkable(buildNavGrid(layout), pose)).toBe(true);
      const doors = layout.furniture.find((f) => f.kind === "elevator")!;
      // One step along the heading leads away from the doors.
      const ahead = { x: pose.x + Math.sin(pose.heading), z: pose.z + Math.cos(pose.heading) };
      expect(Math.hypot(ahead.x - doors.x, ahead.z - doors.z)).toBeGreaterThan(Math.hypot(pose.x - doors.x, pose.z - doors.z));
    }
  });

  it("returns to the remembered spot, and falls back to the spawn without an elevator", () => {
    const layout = buildOfficeLayout(AGENTS);
    expect(arrivalPose(layout, "remembered", { x: 1, z: 2, heading: 0.5 })).toEqual({ x: 1, z: 2, heading: 0.5 });
    const bare = { ...layout, checkpoints: layout.checkpoints.filter((cp) => cp.id !== "elevator") };
    expect(arrivalPose(bare, "elevator")).toEqual({ x: layout.spawn.x, z: layout.spawn.z, heading: Math.PI });
  });

  it("keeps newcomers per floor: the first sight of a floor knows everyone", () => {
    const known = new Set<string>();
    expect(noteArrivals(known, ["p1", "p2"])).toEqual([]);
    expect(noteArrivals(known, ["p1", "p2", "p3"])).toEqual(["p3"]);
    // p1 left and is forgotten; coming back later it arrives again.
    expect(noteArrivals(known, ["p2", "p3"])).toEqual([]);
    expect(noteArrivals(known, ["p1", "p2", "p3"])).toEqual(["p1"]);
    expect(knownOnFloor("agents")).not.toBe(knownOnFloor("coding"));
  });

  it("switching floors forgets the old floor's panels and calls, and remembers where the character stood", () => {
    const store = useOfficeStore.getState();
    store.select({ kind: "agent", id: "a1" });
    store.setNearby({ kind: "checkpoint", id: "elevator" });
    store.summon(["a1"], { x: 0, z: 0 }, 10_000);
    store.requestWalk({ x: 3, z: 3 });
    officeSession.playerPlaced = true;
    Object.assign(player, { x: 4, z: -2, heading: 1, path: [{ x: 5, z: 5 }], moving: true });

    switchFloor("coding", true);
    const after = useOfficeStore.getState();
    expect(after.floor).toBe("coding");
    expect(after.selection).toBeNull();
    expect(after.nearby).toBeNull();
    expect(after.summons).toEqual({});
    expect(after.walkTo).toBeNull();
    expect(player.path).toEqual([]);
    expect(officeSession.floors.agents).toEqual({ x: 4, z: -2, heading: 1 });
    expect(officeSession.arrival).toEqual({ floor: "coding", at: "elevator" });
  });

  it("a mount that asks for a floor returns to the spot remembered there; the elevator always arrives at the doors", () => {
    officeSession.playerPlaced = true;
    officeSession.floors.coding = { x: 7, z: 7, heading: 0 };
    switchFloor("coding", false);
    expect(officeSession.arrival).toEqual({ floor: "coding", at: "remembered" });
    switchFloor("agents", true);
    expect(officeSession.arrival).toEqual({ floor: "agents", at: "elevator" });
    // Asking for the floor already on screen changes nothing.
    switchFloor("agents", false);
    expect(officeSession.arrival).toEqual({ floor: "agents", at: "elevator" });
  });
});
