import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { JarvisAgentsView } from "./JarvisAgentsView";

vi.mock("@/i18n", () => ({ useLocaleChunk: () => true }));
vi.mock("@/components/society/world/WorldStage", () => ({ WorldStage: () => { throw new Error("Legacy world must not mount"); } }));
vi.mock("@/components/society/mars/MarsWorldStage", () => ({ MarsWorldStage: () => { throw new Error("Mars must not mount as the map"); } }));
vi.mock("@/components/society/office/OfficeStage", () => ({ OfficeStage: ({ onOpenLedger, onSelectAgent }: any) => <div data-testid="office-map"><button onClick={onOpenLedger}>Open agents</button><button onClick={() => onSelectAgent("a1")}>Agent a1</button></div> }));
const initialUrl = window.location.href;
afterEach(() => { cleanup(); window.history.replaceState(null, "", initialUrl); });

it.each(["?view=agents", "?view=agents&world=legacy", "?view=agents&world=mars"])("mounts only the office for %s", async (url) => {
  window.history.replaceState(null, "", url);
  const openAgents = vi.fn();
  render(<JarvisAgentsView onOpenAgents={openAgents} />);
  expect(await screen.findByTestId("office-map")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /previous|preview/i })).toBeNull();
  fireEvent.click(screen.getByText("Open agents"));
  expect(openAgents).toHaveBeenCalledOnce();
});

it("forwards an agent picked in the office", async () => {
  const onSelect = vi.fn();
  render(<JarvisAgentsView onOpenAgents={() => undefined} onSelectAgent={onSelect} />);
  fireEvent.click(await screen.findByText("Agent a1"));
  expect(onSelect).toHaveBeenCalledWith("a1");
});
