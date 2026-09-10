import { describe, expect, it } from "vitest";
import {
  CAMERA_BOUNDS, checkpointStop, createRouteGraph, defaultPlacements, DISTRICTS,
  district, distance, graphWalkways, localWorkplaces, node, PLOTS, RAIL_POINTS,
  restoreLayout, route, STOPS, transformPoint, validPlacement,
} from "./cityModel";
import {
  addCitizen, advanceHour, cancelJourney, CAPACITY, createSimulation, CYCLE,
  DWELL, LEG_RIDES, moveBuilding, nextDeparture, previewCrossing, safeDestination,
  setTarget, setTaskActive, STARTUP_HOLD, stepSimulation, stepWithBudget, trainAt,
  type CitySimulation, type Citizen,
} from "./citySimulation";

function task(sim: CitySimulation, id: string, target: Citizen["target"], revision = 1): Citizen {
  const c = addCitizen(sim, id);
  setTarget(sim, id, target, revision);
  setTaskActive(sim, id, true);
  return c;
}
function until(sim: CitySimulation, predicate: () => boolean, timeout = 1200): void {
  const deadline = sim.time + timeout;
  while (!predicate() && sim.time < deadline) stepSimulation(sim, 0.1);
  expect(predicate(), `Condition not reached by ${sim.time.toFixed(1)} seconds`).toBe(true);
}

describe("city graph and authored layout", () => {
  it("contains all five functional destinations with the matching scene anchors", () => {
    expect(DISTRICTS).toHaveLength(5);
    expect(new Set(DISTRICTS.map((d) => d.stop)).size).toBe(5);
    for (const d of DISTRICTS) {
      expect(node(`${d.stop}:entry`)!.position).toEqual(transformPoint(d.buildingPosition, d.buildingRotation, [0, 0, -19]));
      d.workplaces.forEach((p, i) => expect(node(`${d.stop}:work:${i}`)!.position).toEqual(p));
      expect(route(`${d.stop}:platform`, `${d.stop}:work:29`)).not.toBeNull();
    }
  });
  it("renders precisely the external graph links and closes its rail ring", () => {
    const graph = createRouteGraph();
    for (const path of graphWalkways(graph)) {
      const [from, to] = path.id.split("/");
      expect(path.a).toEqual(node(from, graph)!.position);
      expect(path.b).toEqual(node(to, graph)!.position);
    }
    expect(distance(RAIL_POINTS[0], RAIL_POINTS.at(-1)!)).toBeLessThan(1e-8);
    expect(CAMERA_BOUNDS.minX).toBeGreaterThan(-900);
    expect(CAMERA_BOUNDS.maxZ).toBeLessThan(600);
  });
  it("never changes floors where bridge and ground overlap", () => {
    expect(node("cross:ground")!.position[0]).toBe(node("cross:bridge")!.position[0]);
    expect(route("cross:ground", "cross:bridge")).toEqual(["cross:ground", "bridge:base", "bridge:south", "cross:bridge"]);
    expect(route("cross:ground", "cross:bridge", new Set(["bridge:base"]))).toBeNull();
  });
  it("approaches workstations in front of chairs rather than walking through them", () => {
    const path = route("east:entry", "east:work:4")!;
    expect(path.at(-2)).toBe("east:approach:4");
    const p = localWorkplaces("east")[4], d = district("east");
    expect(node("east:approach:4")!.position).toEqual(transformPoint(d.buildingPosition, d.buildingRotation, [p[0], 0, p[2] - 1.5]));
  });
  it("routes semantic checkpoints to the corresponding five districts", () => {
    expect(checkpointStop("desk")).toBe("east");
    expect(checkpointStop("hub:cli")).toBe("east");
    expect(checkpointStop("meeting")).toBe("west");
    expect(checkpointStop("archive")).toBe("knowledge");
    expect(checkpointStop("hub:plugins")).toBe("workshop");
    expect(checkpointStop("hub:web")).toBe("communications");
    expect(checkpointStop("unknown")).toBeNull();
  });
  it("rejects overlap, malformed coordinates and off-grid placements", () => {
    const p = PLOTS[0];
    expect(validPlacement(p, p.x, p.z, 58, 58, [])).toBe(true);
    expect(validPlacement(p, p.x + 1, p.z, 58, 58, [])).toBe(false);
    expect(validPlacement(p, p.x, p.z, 58, 58, [p])).toBe(false);
    expect(validPlacement(p, NaN, p.z, 58, 58, [])).toBe(false);
    expect(validPlacement(p, p.x, p.z, 0, 58, [])).toBe(false);
    expect(validPlacement(p, p.x + 100, p.z, 58, 58, [])).toBe(false);
  });
  it("migrates legacy coordinates and validates each saved plot independently", () => {
    const defaults = defaultPlacements();
    expect(restoreLayout({ version: 1, placements: { west: [-120, 0, 104] } }).placements).toEqual(defaults);
    const changed = { ...defaults, west: [defaults.west[0] + 4, 0, defaults.west[2]], east: [Infinity, 0, 0] };
    const restored = restoreLayout({ version: 2, placements: changed });
    expect(restored.placements.west).toEqual(changed.west);
    expect(restored.placements.east).toEqual(defaults.east);
    expect(restoreLayout(null).placements).toEqual(defaults);
  });
  it("updates the actual entrance route when an empty building is moved", () => {
    const sim = createSimulation(), d = district("east");
    const before = node("east:entry", sim.graph)!.position;
    expect(moveBuilding(sim, "east", [d.x + 4, 0, d.z])).toBe(true);
    expect(node("east:entry", sim.graph)!.position[0]).toBeCloseTo(before[0] + 4);
    const c = addCitizen(sim, "inside");
    c.at = "east:work:0";
    expect(moveBuilding(sim, "east", [d.x, 0, d.z])).toBe(false);
  });
});

describe("physical city journeys", () => {
  it("boards, rides, alights and operates a real terminal workstation without snapping", () => {
    const sim = createSimulation(), c = task(sim, "one", "east");
    const stages = new Set<string>();
    let last = [...c.position] as Citizen["position"];
    until(sim, () => {
      stages.add(c.stage);
      expect(distance(c.position, last)).toBeLessThan(2.8);
      last = [...c.position];
      return c.stage === "working";
    });
    for (const stage of ["walking", "waiting", "boarding", "riding", "exiting", "working"]) expect(stages.has(stage), stage).toBe(true);
    expect(c.atWork).toBe(true);
    expect(c.at).toBe(`east:work:${c.workIndex}`);
    expect(c.position).toEqual(node(c.at, sim.graph)!.position);
    expect(c.heading).toBe(district("east").buildingRotation);
    expect(c.seat).toBeNull();
  });
  it("keeps working intent separate from the physical location", () => {
    const sim = createSimulation(), c = task(sim, "one", "east");
    expect(c.taskActive).toBe(true);
    expect(c.atWork).toBe(false);
    expect(c.at).toBe("west:plaza");
    until(sim, () => c.stage === "working");
    setTaskActive(sim, c.id, false);
    expect(c.stage).toBe("arrived");
    expect(c.atWork).toBe(true);
  });
  it("crosses the actual platform door only while the metro is stopped", () => {
    const sim = createSimulation(), c = task(sim, "one", "east");
    until(sim, () => c.stage === "boarding");
    let closest = Infinity;
    until(sim, () => {
      if (c.stage !== "boarding") return true;
      const train = trainAt(sim.time);
      expect(train.stop).toBe("west");
      expect(train.doorsOpen).toBe(true);
      closest = Math.min(closest, distance(c.position, transformPoint(train.position, train.heading, [0, 0.08, 2.55])));
      return false;
    });
    expect(closest).toBeLessThan(0.2);
    expect(c.stage).toBe("riding");
  });
  it("reaches every district around the connected city", () => {
    for (const stop of STOPS) {
      const sim = createSimulation(), c = task(sim, "one", stop);
      until(sim, () => c.stage === "working");
      expect(c.at).toBe(`${stop}:work:0`);
    }
  });
  it("retains passengers through intermediate ring stops", () => {
    const sim = createSimulation(), c = task(sim, "one", "knowledge");
    until(sim, () => c.stage === "riding");
    until(sim, () => trainAt(sim.time).stop === "east");
    expect(c.stage).toBe("riding");
    expect(c.alightAt).toBe("knowledge");
    until(sim, () => c.stage === "working");
    expect(c.at).toBe("knowledge:work:0");
  });
  it("reserves seats and retains a queue when a train fills", () => {
    const sim = createSimulation();
    for (let i = 0; i < CAPACITY + 3; i++) {
      const c = addCitizen(sim, String(i));
      c.at = "west:platform"; c.position = [...node(c.at, sim.graph)!.position];
      setTarget(sim, c.id, "east", 1); setTaskActive(sim, c.id, true);
    }
    stepSimulation(sim, 15);
    const passengers = [...sim.citizens.values()].filter((c) => c.seat !== null);
    expect(passengers).toHaveLength(CAPACITY);
    expect(new Set(passengers.map((c) => c.seat)).size).toBe(CAPACITY);
    expect([...sim.citizens.values()].filter((c) => c.stage === "waiting")).toHaveLength(3);
    until(sim, () => [...sim.citizens.values()].every((c) => c.stage === "working"));
    expect(new Set([...sim.citizens.values()].map((c) => c.workIndex)).size).toBe(CAPACITY + 3);
  });
  it("misses a departure when there is insufficient time to board", () => {
    const sim = createSimulation();
    sim.time = STARTUP_HOLD + DWELL - 1;
    const c = addCitizen(sim, "one"); c.at = "west:platform"; c.position = [...node(c.at, sim.graph)!.position];
    c.target = "east"; c.taskActive = true; c.stage = "waiting"; c.alightAt = "east";
    stepSimulation(sim, 0.2);
    expect(c.stage).toBe("waiting"); expect(c.seat).toBeNull();
    until(sim, () => c.stage === "working");
  });
  it("leaves a workspace queue for a preview walk without stale queue replanning", () => {
    const sim = createSimulation();
    for (let i = 0; i < 31; i++) task(sim, String(i), "west");
    const c = sim.citizens.get("30")!;
    until(sim, () => c.waitingForWorkspace);
    expect(previewCrossing(sim, c.id)).toBe(true);
    expect(c.waitingForWorkspace).toBe(false);
    stepSimulation(sim, 1);
    const before = [...c.position] as Citizen["position"];
    setTaskActive(sim, c.id, false);
    stepSimulation(sim, 0.1);
    expect(distance(c.position, before)).toBeLessThan(0.3);
    until(sim, () => c.at === "bridge:north");
  });
  it("queues a full workplace and reaches a newly vacated desk physically", () => {
    const sim = createSimulation();
    for (let i = 0; i < 31; i++) task(sim, String(i), "west");
    const waiting = sim.citizens.get("30")!;
    until(sim, () => waiting.waitingForWorkspace);
    expect(waiting.stage).toBe("waiting");
    expect(waiting.atWork).toBe(false);
    until(sim, () => sim.citizens.get("0")!.stage === "working");
    cancelJourney(sim, "0", 2);
    const before = [...waiting.position];
    stepSimulation(sim, 0.1);
    expect(distance(waiting.position, before as Citizen["position"])).toBeLessThan(0.3);
    until(sim, () => waiting.stage === "working");
    expect(waiting.workIndex).toBe(0);
    expect(waiting.waitingForWorkspace).toBe(false);
    expect(waiting.position).toEqual(node("west:work:0", sim.graph)!.position);
  });
  it("retargets on board at the next station and ignores older activity", () => {
    const sim = createSimulation(), c = task(sim, "one", "knowledge");
    until(sim, () => c.stage === "riding");
    const p = [...c.position];
    setTarget(sim, c.id, "west", 3); setTarget(sim, c.id, "knowledge", 2);
    expect(c.position).toEqual(p); expect(c.target).toBe("west");
    expect(c.alightAt).toBe("east");
    until(sim, () => c.stage === "exiting");
    expect(c.alightAt).toBe("east");
    until(sim, () => c.stage === "working");
    expect(c.at).toBe("west:work:0");
  });
  it("does not force a passenger off at an intermediate stop for a same-target update", () => {
    const sim = createSimulation(), c = task(sim, "one", "knowledge");
    until(sim, () => c.stage === "riding");
    setTarget(sim, c.id, "knowledge", 2);
    expect(c.alightAt).toBe("knowledge");
  });
  it("defers a late-dwell retarget until a stop with enough time to exit", () => {
    const sim = createSimulation(), c = task(sim, "one", "knowledge");
    until(sim, () => trainAt(sim.time).stop === "east" && trainAt(sim.time).remaining < 1);
    expect(c.stage).toBe("riding");
    expect(safeDestination(sim, c)).toBe("knowledge");
    setTarget(sim, c.id, "west", 2);
    expect(c.alightAt).toBe("knowledge");
    stepSimulation(sim, 2);
    expect(c.stage).toBe("riding");
    expect(trainAt(sim.time).doorsOpen).toBe(false);
    until(sim, () => c.stage === "exiting");
    expect(trainAt(sim.time).stop).toBe("knowledge");
    until(sim, () => c.stage === "working");
    expect(c.at).toBe("west:work:0");
  });
  it("cancels to a safe station without teleporting or operating an old terminal", () => {
    const sim = createSimulation(), c = task(sim, "one", "knowledge");
    until(sim, () => c.stage === "riding");
    const before = [...c.position];
    cancelJourney(sim, c.id, 2);
    expect(c.position).toEqual(before); expect(c.taskActive).toBe(false);
    until(sim, () => c.stage === "arrived");
    expect(c.at).toBe("east:plaza"); expect(c.atWork).toBe(false);
  });
  it("settles at the physical arrival station when work ends while exiting", () => {
    const sim = createSimulation(), c = task(sim, "one", "east");
    until(sim, () => c.stage === "exiting");
    expect(safeDestination(sim, c)).toBe("east");
    cancelJourney(sim, c.id, 2);
    until(sim, () => c.stage === "arrived");
    expect(c.at).toBe("east:plaza");
  });
  it("cancels a local approach after its current physical edge", () => {
    const sim = createSimulation(), c = task(sim, "one", "west");
    stepSimulation(sim, 1);
    const p = [...c.position];
    cancelJourney(sim, c.id, 2);
    expect(c.position).toEqual(p);
    expect(c.path.length).toBeLessThanOrEqual(2);
    until(sim, () => c.stage === "arrived");
    expect(c.at).toBe("west:plaza");
    expect(c.workIndex).toBeNull();
  });
  it("physically returns to a workstation after cancelling and reactivating", () => {
    const sim = createSimulation(), c = task(sim, "one", "west");
    until(sim, () => c.stage === "working");
    cancelJourney(sim, c.id, 2);
    stepSimulation(sim, 1);
    expect(c.stage).toBe("walking");
    expect(c.atWork).toBe(false);
    const p = [...c.position];
    setTaskActive(sim, c.id, true);
    expect(c.position).toEqual(p);
    expect(c.stage).toBe("walking");
    until(sim, () => c.stage === "working");
    expect(c.at).toBe("west:work:0");
    expect(c.position).toEqual(node(c.at, sim.graph)!.position);
  });
  it("reports unreachable targets and resumes on a newer intent", () => {
    const sim = createSimulation();
    sim.blocked.add("east:entry");
    const c = task(sim, "one", "east");
    // The preexisting access leg is completed before the blocked interior is reconsidered.
    until(sim, () => c.stage === "unreachable");
    sim.blocked.clear(); setTarget(sim, c.id, "east", 2);
    until(sim, () => c.stage === "working");
  });
  it("walks the preview bridge at its exact deck height", () => {
    const sim = createSimulation(), c = addCitizen(sim, "one");
    expect(previewCrossing(sim, c.id)).toBe(true);
    until(sim, () => c.at === "cross:bridge");
    expect(c.position[1]).toBe(20);
    until(sim, () => c.at === "bridge:north");
    expect(c.stage).toBe("arrived");
  });
  it("does not reset a journey when the same roster citizen is observed again", () => {
    const sim = createSimulation(), c = task(sim, "one", "east");
    stepSimulation(sim, 20);
    const p = [...c.position];
    expect(addCitizen(sim, c.id)).toBe(c); expect(c.position).toEqual(p);
  });
});

describe("train clock and bounded catch-up", () => {
  it("keeps every train arrival and departure position continuous", () => {
    let phase = STARTUP_HOLD;
    for (const duration of LEG_RIDES) {
      for (const time of [phase, phase + DWELL, phase + DWELL + duration]) expect(distance(trainAt(time - 0.001).position, trainAt(time).position)).toBeLessThan(0.03);
      phase += DWELL + duration;
    }
    expect(trainAt(STARTUP_HOLD + CYCLE + 1).stop).toBe("west");
  });
  it("reserves the occupied block and closes its doors while moving", () => {
    expect(trainAt(0).reservedBlock).toBe("platform:west");
    const moving = trainAt(STARTUP_HOLD + DWELL + 10);
    expect(moving.doorsOpen).toBe(false); expect(moving.reservedBlock).toBe("track:west:east");
    expect(nextDeparture("west", 0)).toBe(STARTUP_HOLD + DWELL);
  });
  it("matches continuous travel when processing a hidden interval", () => {
    const a = createSimulation(), b = createSimulation();
    task(a, "one", "knowledge"); task(b, "one", "knowledge");
    stepSimulation(a, 90);
    for (let i = 0; i < 900; i++) stepSimulation(b, 0.1);
    expect(a.citizens.get("one")!.stage).toBe(b.citizens.get("one")!.stage);
    expect(distance(a.citizens.get("one")!.position, b.citizens.get("one")!.position)).toBeLessThan(1e-6);
  });
  it("limits catch-up work while retaining every elapsed second", () => {
    const sim = createSimulation(), reference = createSimulation();
    task(sim, "one", "knowledge"); task(reference, "one", "knowledge");
    stepWithBudget(sim, 90, 20);
    expect(sim.time).toBeCloseTo(2); expect(sim.debt).toBeCloseTo(88);
    while (sim.debt > 0.00001) stepWithBudget(sim, 0, 20);
    stepSimulation(reference, 90);
    expect(distance(sim.citizens.get("one")!.position, reference.citizens.get("one")!.position)).toBeLessThan(1e-6);
  });
  it("skips empty idle intervals without moving any citizen", () => {
    const sim = createSimulation(), c = addCitizen(sim, "one");
    const p = [...c.position];
    stepWithBudget(sim, 3600, 5);
    expect(sim.time).toBe(3600); expect(sim.debt).toBe(0); expect(c.position).toEqual(p);
  });
  it("pauses and wraps the independent 30-minute daylight cycle", () => {
    expect(advanceHour(10, 1800, false)).toBe(10);
    expect(advanceHour(10, 300, true)).toBe(10);
  });
});
