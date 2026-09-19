import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useSocietyShell } from "@/store/societyShell";
import { setMapFullscreen } from "@/lib/mapFullscreen";
import { SocietyView } from "./SocietyView";

vi.mock("@/lib/mapFullscreen", () => ({ setMapFullscreen: vi.fn(async () => undefined) }));
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key, useLocaleChunk: () => true }));
vi.mock("@/components/society/chat/useModelMenuData", () => ({ useModelMenuData: () => undefined }));
vi.mock("@/components/society/data", () => ({ useSocietyRoster: () => ({ data: { sample: false, agents: [
  { agentId: "lead", name: "Lead", tier: "lead", state: "idle" },
  { agentId: "specialist", name: "Specialist", tier: "specialist", state: "idle" },
] }, isLoading: false }) }));
vi.mock("@/views/JarvisAgentsView", () => ({ JarvisAgentsView: ({ onSelectAgent, onOpenAgents, onMarsSelectionChange }: any) => (
  <div data-testid="map"><button onClick={() => onSelectAgent("specialist")}>Map specialist</button><button onClick={onOpenAgents}>Map fallback</button><div data-mars-ui><input aria-label="Mars draft" /></div><div data-mars-mode="player"><button>Player viewport</button></div><button onClick={() => onMarsSelectionChange(false)}>Previous world</button><button onClick={() => onMarsSelectionChange(true)}>Mars world</button></div>
) }));
vi.mock("@/components/society/mars/MarsStationPanel", () => ({ MarsStationPanel: ({ onClose }: any) => <aside aria-label="Mars station"><button onClick={onClose}>Close station</button></aside> }));
vi.mock("@/components/society/card/AgentCardOverlay", () => ({ AgentCardOverlay: ({ agent, embedded, onSelectAgent, onCreate }: any) => (
  <div data-testid="workspace" data-embedded={String(embedded)}>
    <span>{agent.name}</span><input aria-label="Draft" />
    <button onClick={() => onSelectAgent("specialist")}>Select specialist</button>
    <button onClick={onCreate}>Create agent</button>
  </div>
) }));
vi.mock("@/components/society/roster/RosterRail", () => ({ RosterRail: () => <div data-testid="roster" /> }));
vi.mock("@/components/society/card/BuildingCardOverlay", () => ({ BuildingCardOverlay: () => null }));
vi.mock("@/components/society/create/CreateAgentDialog", () => ({ CreateAgentDialog: ({ open, onClose }: any) => open ? <button onClick={onClose}>Close creator</button> : null }));

const initialUrl = window.location.href;
afterEach(() => { cleanup(); window.history.replaceState(null, "", initialUrl); });

it("defaults to the embedded Agents workspace even with a saved legacy ledger preference", () => {
  localStorage.setItem("jarvis.agents.mode.v2", "ledger");
  render(<SocietyView />);
  expect(screen.getByTestId("workspace").getAttribute("data-embedded")).toBe("true");
  expect(screen.getByText("Lead")).toBeTruthy();
  expect(screen.queryByTestId("map")).toBeNull();
  localStorage.removeItem("jarvis.agents.mode.v2");
});

it("switches to Map and back without losing the selected agent or draft", async () => {
  render(<SocietyView />);
  fireEvent.click(screen.getByText("Select specialist"));
  const draft = screen.getByLabelText("Draft") as HTMLInputElement;
  fireEvent.change(draft, { target: { value: "Unsent message" } });
  fireEvent.click(screen.getByRole("tab", { name: "society.world.mode_map" }));
  expect(await screen.findByTestId("map")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "society.roster.title" }));
  expect(screen.queryByTestId("map")).toBeNull();
  expect(screen.getByText("Specialist")).toBeTruthy();
  expect(screen.getByLabelText("Draft")).toBe(draft);
  expect(draft.value).toBe("Unsent message");
});

it("opens map selections in Agents and keeps creation available", async () => {
  render(<SocietyView />);
  fireEvent.click(screen.getByRole("tab", { name: "society.world.mode_map" }));
  fireEvent.click(await screen.findByText("Map specialist"));
  expect(screen.queryByTestId("map")).toBeNull();
  expect(screen.getByText("Specialist")).toBeTruthy();
  fireEvent.click(screen.getByText("Create agent"));
  fireEvent.click(screen.getByText("Close creator"));
  expect(screen.queryByText("Close creator")).toBeNull();
});


it("starts with sections hidden and toggles them independently of the agent roster", () => {
  render(<SocietyView />);
  expect(useSocietyShell.getState().navigationOpen).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "society.world.toggle_sections" }));
  expect(useSocietyShell.getState().navigationOpen).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "society.world.toggle_sections" }));
  expect(useSocietyShell.getState().navigationOpen).toBe(false);
});

it("requests fullscreen for Map and leaves it on Escape", async () => {
  render(<SocietyView />);
  fireEvent.click(screen.getByRole("tab", { name: "society.world.mode_map" }));
  await screen.findByTestId("map");
  expect(setMapFullscreen).toHaveBeenLastCalledWith(true);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(setMapFullscreen).toHaveBeenLastCalledWith(false);
  expect(screen.queryByTestId("map")).toBeNull();
});

it("keeps Mars station controls reachable without mounting a renderer", async () => {
  window.history.replaceState(null, "", "?view=agents&world=mars");
  render(<SocietyView />);
  fireEvent.click(screen.getByRole("button", { name: "society.mars.station_title" }));
  expect(await screen.findByRole("complementary", { name: "Mars station" })).toBeTruthy();
  expect(screen.queryByTestId("map")).toBeNull();
  expect(screen.getByTestId("workspace")).toBeTruthy();
  fireEvent.click(screen.getByText("Close station"));
  expect(screen.queryByRole("complementary", { name: "Mars station" })).toBeNull();
});

it("does not discard a Mars form when Escape belongs to its input", async () => {
  render(<SocietyView />);
  fireEvent.click(screen.getByRole("tab", { name: "society.world.mode_map" }));
  const field = await screen.findByLabelText("Mars draft");
  fireEvent.change(field, { target: { value: "Unsent station draft" } });
  fireEvent.keyDown(field, { key: "Escape" });
  expect(screen.getByTestId("map")).toBeTruthy();
  expect((field as HTMLInputElement).value).toBe("Unsent station draft");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByTestId("map")).toBeNull();
});

it.each(["Mars draft", "Player viewport"])("preserves focused %s when the browser exits fullscreen without a keydown", async (target) => {
  render(<SocietyView />);
  fireEvent.click(screen.getByRole("tab", { name: "society.world.mode_map" }));
  const field = await screen.findByLabelText("Mars draft");
  fireEvent.change(field, { target: { value: "Unsent station draft" } });
  const focused = target === "Mars draft" ? field : screen.getByText(target);
  focused.focus();
  expect(document.fullscreenElement).toBeFalsy();
  fireEvent(document, new Event("fullscreenchange"));
  expect(screen.getByTestId("map")).toBeTruthy();
  expect(screen.getByLabelText("Mars draft")).toBe(field);
  expect((field as HTMLInputElement).value).toBe("Unsent station draft");
  fireEvent.click(screen.getByRole("tab", { name: "society.roster.title" }));
  expect(screen.queryByTestId("map")).toBeNull();
});

it("still leaves the ordinary map when browser fullscreen exits", async () => {
  render(<SocietyView />);
  fireEvent.click(screen.getByRole("tab", { name: "society.world.mode_map" }));
  await screen.findByTestId("map");
  fireEvent(document, new Event("fullscreenchange"));
  expect(screen.queryByTestId("map")).toBeNull();
});

it("clears the open station when Mars is deselected, including a later opt-in", async () => {
  window.history.replaceState(null, "", "?view=agents&world=mars");
  render(<SocietyView />);
  fireEvent.click(screen.getByRole("button", { name: "society.mars.station_title" }));
  await screen.findByRole("complementary", { name: "Mars station" });
  fireEvent.click(screen.getByRole("tab", { name: "society.world.mode_map" }));
  fireEvent.click(await screen.findByText("Previous world"));
  fireEvent.click(screen.getByRole("tab", { name: "society.roster.title" }));
  expect(screen.queryByRole("complementary", { name: "Mars station" })).toBeNull();
  expect(screen.queryByRole("button", { name: "society.mars.station_title" })).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "society.world.mode_map" }));
  fireEvent.click(await screen.findByText("Mars world"));
  fireEvent.click(screen.getByRole("tab", { name: "society.roster.title" }));
  expect(screen.getByRole("button", { name: "society.mars.station_title" })).toBeTruthy();
  expect(screen.queryByRole("complementary", { name: "Mars station" })).toBeNull();
});
