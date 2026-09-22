import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { setMapFullscreen } from "@/lib/mapFullscreen";
import { LAST_AGENT_STORAGE_KEY } from "./lastAgent";
import { SocietyView } from "./SocietyView";

vi.mock("@/lib/mapFullscreen", () => ({ setMapFullscreen: vi.fn(async () => undefined) }));
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key, useLocaleChunk: () => true }));
vi.mock("@/components/society/chat/useModelMenuData", () => ({ useModelMenuData: () => undefined }));
vi.mock("@/components/society/data", () => ({ useSocietyRoster: () => ({ data: { sample: false, agents: [
  { agentId: "lead", name: "Lead", tier: "lead", state: "idle" },
  { agentId: "specialist", name: "Specialist", tier: "specialist", state: "idle" },
] }, isLoading: false }) }));
vi.mock("@/views/JarvisAgentsView", () => ({ JarvisAgentsView: ({ onSelectAgent, onOpenAgents, topRight }: any) => (
  <div data-testid="map">{topRight}<button onClick={() => onSelectAgent("specialist")}>Map specialist</button><button onClick={onOpenAgents}>Map fallback</button></div>
) }));
vi.mock("@/components/society/card/AgentCardOverlay", () => ({ AgentCardOverlay: ({ agent, embedded, onSelectAgent, onCreate, railHeader }: any) => (
  <div data-testid="workspace" data-embedded={String(embedded)}>
    {railHeader}
    <span>{agent.name}</span><input aria-label="Draft" />
    <button onClick={() => onSelectAgent("specialist")}>Select specialist</button>
    <button onClick={onCreate}>Create agent</button>
  </div>
) }));
vi.mock("@/components/society/roster/RosterRail", () => ({ RosterRail: () => <div data-testid="roster" /> }));
vi.mock("@/components/society/card/BuildingCardOverlay", () => ({ BuildingCardOverlay: () => null }));
vi.mock("@/components/society/create/CreateAgentDialog", () => ({ CreateAgentDialog: ({ open, onClose }: any) => open ? <button onClick={onClose}>Close creator</button> : null }));

afterEach(() => {
  cleanup();
  localStorage.removeItem(LAST_AGENT_STORAGE_KEY);
});

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

it("restores the most recently selected agent after the view is remounted", () => {
  const firstVisit = render(<SocietyView />);
  fireEvent.click(screen.getByText("Select specialist"));
  expect(localStorage.getItem(LAST_AGENT_STORAGE_KEY)).toBe("specialist");

  firstVisit.unmount();
  render(<SocietyView />);

  expect(screen.getByText("Specialist")).toBeTruthy();
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


it("floats the Map/Agents switch without a second header bar", async () => {
  render(<SocietyView />);
  // Agents mode carries the switch in the window caption: no second bar, no
  // "Back to app" — the workspace expands straight under the caption.
  expect(screen.queryByRole("button", { name: "settings_hub.back_to_app" })).toBeNull();
  const captionSwitch = screen.getByTestId("agents-mode-switch");
  expect(within(captionSwitch).getByRole("tab", { name: "society.world.mode_map" })).toBeTruthy();
  fireEvent.click(within(captionSwitch).getByRole("tab", { name: "society.world.mode_map" }));
  const map = await screen.findByTestId("map");
  // The switch rides along into the map HUD, so Map stays closable.
  expect(screen.queryByTestId("agents-mode-switch")).toBeNull();
  expect(screen.queryByRole("button", { name: "settings_hub.back_to_app" })).toBeNull();
  expect(within(map).getByRole("tab", { name: "society.roster.title" })).toBeTruthy();
  fireEvent.click(within(map).getByRole("tab", { name: "society.roster.title" }));
  expect(screen.queryByTestId("map")).toBeNull();
  expect(screen.getByTestId("agents-mode-switch")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "settings_hub.back_to_app" })).toBeNull();
});

it("navigates back through the window caption instead of a sections toggle", () => {
  render(<SocietyView />);
  expect(screen.queryByRole("button", { name: "society.world.toggle_sections" })).toBeNull();
  expect(screen.queryByRole("button", { name: "settings_hub.back_to_app" })).toBeNull();
  // The caption sidebar toggle (owned by TopBar) is the way back to the app.
  expect(screen.getByTestId("agents-mode-switch")).toBeTruthy();
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
