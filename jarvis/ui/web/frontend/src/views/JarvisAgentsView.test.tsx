import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { JarvisAgentsView } from "./JarvisAgentsView";

vi.mock("@/i18n", () => ({ useLocaleChunk: () => true }));
vi.mock("@/components/society/world/WorldStage", () => ({ WorldStage: () => { throw new Error("Legacy world must not mount"); } }));
vi.mock("@/components/society/mars/MarsWorldStage", () => ({ MarsWorldStage: ({ onOpenLedger, onOpenStation, stationPanel }: any) => <div data-testid="mars-map"><button onClick={onOpenLedger}>Open agents</button><button onClick={onOpenStation}>Open station</button>{stationPanel}</div> }));
vi.mock("@/components/society/mars/MarsStationPanel", () => ({ MarsStationPanel: ({ onClose }: any) => <aside aria-label="Mars station"><button onClick={onClose}>Close station</button></aside> }));
const initialUrl = window.location.href;
afterEach(() => { cleanup(); window.history.replaceState(null, "", initialUrl); });

it.each(["?view=agents", "?view=agents&world=legacy", "?view=agents&world=mars"])("uses only Mars in the normal app for %s", async (url) => {
  window.history.replaceState(null, "", url);
  const openAgents = vi.fn();
  render(<JarvisAgentsView onOpenAgents={openAgents} />);
  expect(await screen.findByTestId("mars-map")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /previous|preview/i })).toBeNull();
  fireEvent.click(screen.getByText("Open agents"));
  expect(openAgents).toHaveBeenCalledOnce();
});

it("keeps the station usable within the replacement map", async () => {
  render(<JarvisAgentsView onOpenAgents={() => undefined} />);
  fireEvent.click(await screen.findByText("Open station"));
  expect(await screen.findByRole("complementary", { name: "Mars station" })).toBeTruthy();
  fireEvent.click(screen.getByText("Close station"));
  expect(screen.queryByRole("complementary", { name: "Mars station" })).toBeNull();
});
