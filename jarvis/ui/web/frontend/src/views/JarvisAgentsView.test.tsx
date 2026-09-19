import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { JarvisAgentsView } from "./JarvisAgentsView";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key, useLocaleChunk: () => true }));
vi.mock("@/components/society/world/WorldStage", () => ({ WorldStage: ({ topRight, onOpenAgents }: any) => <div data-testid="previous-map">{topRight}<button onClick={onOpenAgents}>Open agents</button></div> }));
vi.mock("@/components/society/mars/MarsWorldStage", () => ({ MarsWorldStage: ({ topRight, onOpenLedger, onOpenStation, stationPanel }: any) => <div data-testid="mars-map">{topRight}<button onClick={onOpenLedger}>Open agents</button><button onClick={onOpenStation}>Open station</button>{stationPanel}</div> }));
vi.mock("@/components/society/mars/MarsStationPanel", () => ({ MarsStationPanel: ({ onClose }: any) => <aside aria-label="Mars station"><button onClick={onClose}>Close station</button></aside> }));
const initialUrl = window.location.href;
afterEach(() => { cleanup(); window.history.replaceState(null, "", initialUrl); });

it("keeps the existing map and parent workspace while making Mars opt-in", async () => {
  window.history.replaceState(null, "", "?view=agents");
  const openAgents = vi.fn();
  const selectMars = vi.fn();
  render(<JarvisAgentsView onOpenAgents={openAgents} onMarsSelectionChange={selectMars} />);
  expect(await screen.findByTestId("previous-map")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "society.mars.open_preview" }));
  expect(await screen.findByTestId("mars-map")).toBeTruthy();
  expect(new URLSearchParams(window.location.search).get("world")).toBe("mars");
  expect(selectMars).toHaveBeenLastCalledWith(true);
  fireEvent.click(screen.getByText("Open agents"));
  expect(openAgents).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByText("Open station"));
  expect(await screen.findByRole("complementary", { name: "Mars station" })).toBeTruthy();
  fireEvent.click(screen.getByText("Close station"));
  expect(screen.queryByRole("complementary", { name: "Mars station" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "society.mars.previous_world" }));
  expect(await screen.findByTestId("previous-map")).toBeTruthy();
  expect(selectMars).toHaveBeenLastCalledWith(false);
});

it("restores the selected Mars map from its URL without restoring the retired Ledger", async () => {
  window.history.replaceState(null, "", "?view=agents&world=mars");
  render(<JarvisAgentsView onOpenAgents={() => undefined} />);
  expect(await screen.findByTestId("mars-map")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "society.mars.previous_world" }));
  expect(await screen.findByTestId("previous-map")).toBeTruthy();
  expect(new URLSearchParams(window.location.search).has("world")).toBe(false);
});
