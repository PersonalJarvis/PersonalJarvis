import { describe, expect, it } from "vitest";
import { node, PLOTS, route, validPlacement } from "./cityModel";
import {
  addCitizen,
  advanceHour,
  CAPACITY,
  createSimulation,
  previewCrossing,
  safeDestination,
  setTarget,
  stepSimulation,
  trainAt,
} from "./citySimulation";

describe("city reference journeys", () => {
  it("never connects overlapping ground and bridge nodes without the lift", () => {
    expect(node("cross:ground")!.position[0]).toBe(
      node("cross:bridge")!.position[0],
    );
    expect(route("cross:ground", "cross:bridge")).toEqual([
      "cross:ground",
      "bridge:base",
      "bridge:south",
      "cross:bridge",
    ]);
    expect(
      route("cross:ground", "cross:bridge", new Set(["bridge:base"])),
    ).toBeNull();
  });
  it("completes a physical rail journey including boarding and exiting", () => {
    const sim = createSimulation();
    const c = addCitizen(sim, "one", "west");
    setTarget(sim, c.id, "east", 1);
    const stages = new Set<string>();
    let last = c.position;
    for (let i = 0; i < 2500; i++) {
      stepSimulation(sim, 0.1);
      stages.add(c.stage);
      expect(Math.hypot(...c.position.map((v, j) => v - last[j]))).toBeLessThan(
        1.6,
      );
      last = [...c.position];
    }
    for (const stage of [
      "walking",
      "waiting",
      "boarding",
      "riding",
      "exiting",
      "arrived",
    ])
      expect(stages.has(stage)).toBe(true);
    expect(c.at).toBe("east:work");
    expect(c.seat).toBeNull();
  });
  it("reserves capacity and leaves excess passengers for the next departure", () => {
    const sim = createSimulation();
    for (let i = 0; i < 12; i++) {
      const c = addCitizen(sim, String(i), "west");
      c.at = "west:platform";
      c.position = [...node(c.at)!.position];
      setTarget(sim, c.id, "east", 1);
    }
    stepSimulation(sim, 9);
    expect(
      [...sim.citizens.values()].filter((c) => c.seat !== null),
    ).toHaveLength(CAPACITY);
    expect(
      [...sim.citizens.values()].filter((c) => c.stage === "waiting"),
    ).toHaveLength(4);
    stepSimulation(sim, 240);
    expect([...sim.citizens.values()].every((c) => c.at === "east:work")).toBe(
      true,
    );
  });
  it("retargets on a train without snapping and ignores older activity", () => {
    const sim = createSimulation();
    const c = addCitizen(sim, "one", "west");
    setTarget(sim, c.id, "east", 1);
    while (c.stage !== "riding" && sim.time < 150) stepSimulation(sim, 0.1);
    expect(c.stage).toBe("riding");
    const before = [...c.position];
    setTarget(sim, c.id, "west", 3);
    setTarget(sim, c.id, "east", 2);
    expect(c.position).toEqual(before);
    expect(c.target).toBe("west");
    stepSimulation(sim, 250);
    expect(c.at).toBe("west:work");
  });
  it("reports a blocked destination and resumes after an updated intent", () => {
    const sim = createSimulation();
    const c = addCitizen(sim, "one", "west");
    sim.blocked.add("east:work");
    setTarget(sim, c.id, "east", 1);
    expect(c.stage).toBe("unreachable");
    sim.blocked.clear();
    setTarget(sim, c.id, "east", 2);
    expect(c.stage).toBe("walking");
  });
  it("matches continuous travel when catching up an offscreen interval", () => {
    const a = createSimulation(),
      b = createSimulation();
    for (const sim of [a, b]) {
      addCitizen(sim, "one", "west");
      setTarget(sim, "one", "east", 1);
    }
    stepSimulation(a, 90);
    for (let i = 0; i < 900; i++) stepSimulation(b, 0.1);
    expect(a.citizens.get("one")!.stage).toBe(b.citizens.get("one")!.stage);
    a.citizens
      .get("one")!
      .position.forEach((v, i) =>
        expect(v).toBeCloseTo(b.citizens.get("one")!.position[i], 5),
      );
  });
  it("keeps the train continuous at departure and arrival", () => {
    for (const time of [12, 28, 40, 56])
      expect(
        Math.abs(trainAt(time - 0.001).position[0] - trainAt(time).position[0]),
      ).toBeLessThan(0.02);
  });
  it("walks the preview crossing at its actual deck height", () => {
    const sim = createSimulation();
    const c = addCitizen(sim, "one", "west");
    expect(previewCrossing(sim, c.id)).toBe(true);
    let reachedDeck = false;
    for (let i = 0; i < 3500; i++) {
      stepSimulation(sim, 0.1);
      if (c.at === "cross:bridge") {
        expect(c.position[1]).toBe(20);
        reachedDeck = true;
      }
    }
    expect(reachedDeck).toBe(true);
  });
  it("pauses the day cycle and wraps a full 30 minute day", () => {
    expect(advanceHour(10, 1800, false)).toBe(10);
    expect(advanceHour(10, 300, true)).toBe(10);
  });
  it("rejects overlapping, off-grid or unreachable building placement", () => {
    expect(validPlacement(PLOTS[0], -120, 104, 40, 36, [])).toBe(true);
    expect(validPlacement(PLOTS[0], -119, 104, 40, 36, [])).toBe(false);
    expect(validPlacement(PLOTS[0], -120, 84, 40, 36, [])).toBe(false);
    expect(validPlacement(PLOTS[0], -120, 104, 40, 36, [PLOTS[0]])).toBe(false);
  });
});


it("settles at the arrival station when work ends during alighting", () => {
  const sim = createSimulation(); const c = addCitizen(sim, "one", "west");
  sim.time = 30; c.boardingAt = "west"; c.seat = 0; c.stage = "exiting";
  expect(safeDestination(sim, c)).toBe("east");
});
