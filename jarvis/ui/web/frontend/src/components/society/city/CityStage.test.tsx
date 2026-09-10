import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const fixture = vi.hoisted(() => ({ translations: 0, pending: false, roster: { sample: false, agents: [{ agentId: "real-agent", name: "Agent", figure: null, checkpoint: "idle", state: "idle" }] } }));
vi.mock("@/i18n", () => ({ useLocaleChunk: () => true, useT: () => {
  if (++fixture.translations > 30) throw new Error("City entered a reconciliation loop");
  return (key: string) => key;
} }));
vi.mock("../data", () => ({ useSocietyRoster: () => ({ data: fixture.pending ? undefined : fixture.roster, isError: false }) }));
vi.mock("@/hooks/useWebglSurface", () => ({ useWebglSurface: () => ({ generation: 0 }) }));
vi.mock("@/hooks/useCanvasAwake", () => ({ useCanvasAwake: () => false }));
vi.mock("@/lib/graphDimension", () => ({ useWebglSupported: () => false }));
vi.mock("./CityEnvironment", () => ({ CityEnvironment: () => null }));
vi.mock("./CityRuntime", () => ({ CityRuntime: () => null, MovingActor: () => null, OVERVIEW: { position: [0, 0, 0], distance: 100, yaw: 0, key: 0 } }));

import { CityStage } from "./CityStage";
import { citySessions } from "./citySession";
import { defaultPlacements } from "./cityModel";
import { addCitizen, setTarget, stepSimulation } from "./citySimulation";
afterEach(() => { cleanup(); fixture.translations = 0; fixture.pending = false; fixture.roster.sample = false; });
it("settles roster reconciliation with an unstable translation function", () => {
  const view = render(<CityStage onOpenLedger={() => undefined} />);
  expect(view.getByTestId("city-reference").getAttribute("data-version")).toBe("1");
  expect(fixture.translations).toBeLessThan(10);
});
it("discloses successful sample responses instead of claiming a live feed", () => {
  fixture.roster.sample = true;
  const view = render(<CityStage onOpenLedger={() => undefined} />);
  expect(view.getByText("society.city.sample_notice")).toBeTruthy();
  expect(view.queryByText("society.city.live")).toBeNull();
});
it("retains cached live journeys while a returning roster fetch is pending", () => {
  const live = citySessions.get("live", defaultPlacements, performance.now());
  const citizen = addCitizen(live.sim, "real-agent", "west");
  setTarget(live.sim, citizen.id, "east", citizen.revision + 1); stepSimulation(live.sim, 10);
  const position = [...citizen.position]; fixture.pending = true;
  const view = render(<CityStage onOpenLedger={() => undefined} />);
  expect(live.sim.citizens.get(citizen.id)).toBe(citizen);
  fixture.pending = false; view.rerender(<CityStage onOpenLedger={() => undefined} />);
  expect(live.sim.citizens.get(citizen.id)).toBe(citizen); expect(citizen.position).toEqual(position);
});
it("never replaces live journeys with offline sample actors", () => {
  const live = citySessions.get("live", defaultPlacements, performance.now());
  const citizen = live.sim.citizens.get("real-agent");
  fixture.roster.sample = true; render(<CityStage onOpenLedger={() => undefined} />);
  expect(live.sim.citizens.get("real-agent")).toBe(citizen);
});
