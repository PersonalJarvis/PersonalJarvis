import { describe, expect, it } from "vitest";
import { addCitizen, createSimulation, setTarget } from "./citySimulation";
import { createCityClock, tickCityClock } from "./cityClock";

describe("shared city clock", () => {
  it("does not double-count a frame and timer at the same time", () => {
    const sim = createSimulation(),
      clock = createCityClock(0);
    tickCityClock(clock, sim, 1000, false);
    tickCityClock(clock, sim, 1000, false);
    expect(sim.time).toBeCloseTo(1);
    tickCityClock(clock, sim, 1500, false);
    expect(sim.time).toBeCloseTo(1.5);
  });
  it("freezes journeys and daylight and does not replay the paused interval", () => {
    const sim = createSimulation(),
      clock = createCityClock(0);
    tickCityClock(clock, sim, 30000, true);
    expect(sim.time).toBe(0);
    expect(clock.hour).toBe(10);
    tickCityClock(clock, sim, 31000, false);
    expect(sim.time).toBeCloseTo(1);
  });
  it("budgets active catch-up and retains remaining physical time", () => {
    const sim = createSimulation(),
      clock = createCityClock(0);
    addCitizen(sim, "commuter", "west");
    setTarget(sim, "commuter", "east", 1);
    tickCityClock(clock, sim, 120000, false);
    expect(sim.time).toBeLessThan(10);
    expect(sim.debt).toBeGreaterThan(110);
    for (let i = 0; i < 100; i++) tickCityClock(clock, sim, 120000, false);
    expect(sim.time).toBeCloseTo(120);
    expect(sim.debt).toBeLessThan(0.0001);
  });
});
