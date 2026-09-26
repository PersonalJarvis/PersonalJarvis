import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { setMapFullscreen } from "@/lib/mapFullscreen";
import { LAST_AGENT_STORAGE_KEY } from "./lastAgent";
import { SocietyView } from "./SocietyView";

const app = vi.hoisted(() => ({ instance: { name: "default", isDev: false }, desktop: false }));
vi.mock("@/lib/nativeDrop", () => ({ inDesktopShell: () => app.desktop }));
vi.mock("@/hooks/useAppInstance", () => ({ useAppInstance: () => app.instance }));
beforeEach(() => { app.instance = { name: "default", isDev: false }; app.desktop = false; });

vi.mock("@/lib/mapFullscreen", () => ({ setMapFullscreen: vi.fn(async () => undefined) }));
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key, useLocaleChunk: () => true }));
vi.mock("@/components/society/chat/useModelMenuData", () => ({ useModelMenuData: () => undefined }));
vi.mock("@/components/society/data", () => ({ useSocietyRoster: () => ({ data: { sample: false, agents: [
  { agentId: "lead", name: "Lead", tier: "lead", state: "idle" },
  { agentId: "specialist", name: "Specialist", tier: "specialist", state: "idle" },
] }, isLoading: false }) }));
vi.mock("@/views/JarvisAgentsView", () => ({ JarvisAgentsView: ({ onSelectAgent, onOpenAgents, onMarsSelectionChange }: any) => (
  <div data-testid="map"><button onClick={() => onSelectAgent("specialist")}>Map specialist</button><button onClick={onOpenAgents}>Map fallback</button><div data-mars-ui><input aria-label="Mars draft" /></div><div data-mars-mode="player"><button>Player viewport</button></div><div data-mars-mode="follow"><button>Follow viewport</button></div><button onClick={() => onMarsSelectionChange(false)}>Previous world</button><button onClick={() => onMarsSelectionChange(true)}>Mars world</button></div>
) }));
vi.mock("@/components/society/mars/MarsStationPanel", () => ({ MarsStationPanel: ({ onClose }: any) => <aside aria-label="Mars station"><button onClick={onClose}>Close station</button></aside> }));
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

const initialUrl = window.location.href;
afterEach(() => { cleanup(); window.history.replaceState(null, "", initialUrl); localStorage.removeItem(LAST_AGENT_STORAGE_KEY); });

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


it("keeps the Map/Agents switch in the caption in both modes", async () => {
  render(<SocietyView />);
  // Agents mode: no second bar, no "Back to app" — the workspace expands
  // straight under the caption, the switch rides centered in it.
  expect(screen.queryByRole("button", { name: "settings_hub.back_to_app" })).toBeNull();
  const captionSwitch = screen.getByTestId("mode-switch");
  expect(within(captionSwitch).getByRole("tab", { name: "society.world.mode_map" })).toBeTruthy();
  fireEvent.click(within(captionSwitch).getByRole("tab", { name: "society.world.mode_map" }));
  await screen.findByTestId("map");
  // Map mode takes the native window fullscreen: the switch stays in the
  // caption instead of shrinking into the map HUD, so Agents stays reachable.
  expect(screen.getByTestId("mode-switch")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "settings_hub.back_to_app" })).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "society.roster.title" }));
  expect(screen.queryByTestId("map")).toBeNull();
  expect(screen.getByTestId("mode-switch")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "settings_hub.back_to_app" })).toBeNull();
});

it("navigates back through the window caption instead of a sections toggle", () => {
  render(<SocietyView />);
  expect(screen.queryByRole("button", { name: "society.world.toggle_sections" })).toBeNull();
  expect(screen.queryByRole("button", { name: "settings_hub.back_to_app" })).toBeNull();
  // The caption sidebar toggle (owned by TopBar) is the way back to the app.
  expect(screen.getByTestId("mode-switch")).toBeTruthy();
});

it("requests fullscreen for Map and leaves it on Escape", async () => {
  app.desktop = true;
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

it("exposes the Mars station in dev without an opt-in URL", async () => {
  app.instance = { name: "dev", isDev: true };
  window.history.replaceState(null, "", "?view=agents");
  render(<SocietyView />);
  fireEvent.click(screen.getByRole("button", { name: "society.mars.station_title" }));
  expect(await screen.findByRole("complementary", { name: "Mars station" })).toBeTruthy();
  expect(screen.queryByTestId("map")).toBeNull();
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

it.each(["Mars draft", "Player viewport", "Follow viewport"])("preserves focused %s when the browser exits fullscreen without a keydown", async (target) => {
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


