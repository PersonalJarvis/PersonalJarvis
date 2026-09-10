import { expect, it } from "vitest";
import { defaultPlacements } from "./cityModel";
import { addCitizen, setTarget, stepSimulation } from "./citySimulation";
import { createCitySessions } from "./citySession";

it("retains a physical journey when leaving and reopening the city section", () => {
  const sessions = createCitySessions();
  const first = sessions.get("live", defaultPlacements, 0);
  const person = addCitizen(first.sim, "agent", "west"); setTarget(first.sim, person.id, "east", 1); stepSimulation(first.sim, 10);
  const position = [...person.position];
  const reopened = sessions.get("live", defaultPlacements, 50000);
  expect(reopened).toBe(first); expect(reopened.sim.citizens.get(person.id)!.position).toEqual(position);
});
it("keeps demo journeys separate from real agent positions", () => {
  const sessions = createCitySessions(); const live = sessions.get("live", defaultPlacements, 0), demo = sessions.get("demo", defaultPlacements, 0);
  addCitizen(demo.sim, "example", "west"); stepSimulation(demo.sim, 50);
  expect(live.sim.citizens.size).toBe(0); expect(live.sim.time).toBe(0); expect(demo.sim.time).toBeCloseTo(50);
});
