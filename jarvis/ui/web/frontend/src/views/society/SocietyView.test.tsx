import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { setMapFullscreen } from "@/lib/mapFullscreen";
import { LAST_AGENT_STORAGE_KEY } from "./lastAgent";
import { SocietyView } from "./SocietyView";

const app = vi.hoisted(() => ({ instance: { name: "default", isDev: false } }));
const groupsState = vi.hoisted(() => ({ groups: [] as Array<{ group_id: string; name: string; members: string[] }> }));
vi.mock("@/hooks/useAppInstance", () => ({ useAppInstance: () => app.instance }));
beforeEach(() => { app.instance = { name: "default", isDev: false }; groupsState.groups = []; });

vi.mock("@/lib/mapFullscreen", () => ({ setMapFullscreen: vi.fn(async () => undefined) }));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-query")>()),
  useQueryClient: () => ({ invalidateQueries: async () => undefined }),
}));
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key, useLocaleChunk: () => true }));
vi.mock("@/components/society/chat/useModelMenuData", () => ({ useModelMenuData: () => undefined }));
vi.mock("@/lib/societyChatGroups", () => ({ useSocietyChatGroups: () => ({ data: groupsState.groups }) }));
vi.mock("@/components/society/chat/ChatGroupPanel", () => ({ ChatGroupPanel: ({ group, onOpenAgent }: any) => <div data-testid="group-workspace">{group.name}<button onClick={() => onOpenAgent("specialist")}>Open member</button></div> }));
vi.mock("@/components/society/data", () => ({ useSocietyRoster: () => ({ data: { sample: false, agents: [
  { agentId: "lead", name: "Lead", tier: "lead", state: "idle" },
  { agentId: "specialist", name: "Specialist", tier: "specialist", state: "idle" },
] }, isLoading: false }) }));
vi.mock("@/views/JarvisAgentsView", () => ({ JarvisAgentsView: ({ onSelectAgent, onOpenAgents, onMarsSelectionChange }: any) => (
  <div data-testid="map"><button onClick={() => onSelectAgent("specialist")}>Map specialist</button><button onClick={onOpenAgents}>Map fallback</button><div data-mars-ui><input aria-label="Mars draft" /></div><div data-mars-mode="player"><button>Player viewport</button></div><div data-mars-mode="follow"><button>Follow viewport</button></div><button onClick={() => onMarsSelectionChange(false)}>Previous world</button><button onClick={() => onMarsSelectionChange(true)}>Mars world</button></div>
) }));
vi.mock("@/components/society/mars/MarsStationPanel", () => ({ MarsStationPanel: ({ onClose }: any) => <aside aria-label="Mars station"><button onClick={onClose}>Close station</button></aside> }));
vi.mock("@/components/society/card/AgentCardOverlay", () => ({ AgentCardOverlay: ({ agent, embedded, onSelectAgent, onSelectGroup, onCreate, railHeader }: any) => (
  <div data-testid="workspace" data-embedded={String(embedded)}>
    {railHeader}
    <span>{agent.name}</span><input aria-label="Draft" />
    <button onClick={() => onSelectAgent("specialist")}>Select specialist</button>
    <button onClick={() => onSelectGroup?.("team")}>Open group</button>
    <button onClick={onCreate}>Create agent</button>
  </div>
) }));
vi.mock("@/components/society/roster/RosterRail", () => ({ RosterRail: () => <div data-testid="roster" /> }));
vi.mock("@/components/society/ledger/SocietyLedger", () => ({ SocietyLedger: () => <div data-testid="society-ledger">Ledger history</div> }));
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

it("opens a group from the agent rail and returns to an individual member chat", () => {
  groupsState.groups = [{ group_id: "team", name: "Launch team", members: ["specialist"] }];
  render(<SocietyView />);
  fireEvent.click(screen.getByText("Open group"));
  expect(screen.getByTestId("group-workspace").textContent).toContain("Launch team");
  fireEvent.click(screen.getByText("Open member"));
  expect(screen.getByTestId("workspace").textContent).toContain("Specialist");
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

it("opens the durable Ledger from the caption and from the office monitor", async () => {
  render(<SocietyView />);
  fireEvent.click(screen.getByRole("tab", { name: "society.world.mode_ledger" }));
  expect(screen.getByTestId("society-ledger")).toBeTruthy();
  expect(screen.queryByTestId("map")).toBeNull();

  fireEvent.click(screen.getByRole("tab", { name: "society.world.mode_map" }));
  fireEvent.click(await screen.findByText("Map fallback"));
  expect(screen.getByTestId("society-ledger")).toBeTruthy();
  expect(screen.queryByTestId("map")).toBeNull();
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

it("supports roving keyboard navigation across Map, Agents, and Ledger tabs", () => {
  render(<SocietyView />);
  const switcher = screen.getByTestId("mode-switch");
  const map = within(switcher).getByRole("tab", { name: "society.world.mode_map" });
  const agents = within(switcher).getByRole("tab", { name: "society.roster.title" });
  const ledger = within(switcher).getByRole("tab", { name: "society.world.mode_ledger" });

  expect(agents.tabIndex).toBe(0);
  expect(map.tabIndex).toBe(-1);
  expect(ledger.tabIndex).toBe(-1);

  agents.focus();
  fireEvent.keyDown(agents, { key: "ArrowRight" });
  expect(document.activeElement).toBe(ledger);
  expect(ledger.getAttribute("aria-selected")).toBe("true");
  expect(ledger.tabIndex).toBe(0);
  expect(agents.tabIndex).toBe(-1);

  fireEvent.keyDown(ledger, { key: "Home" });
  expect(document.activeElement).toBe(map);
  expect(map.getAttribute("aria-selected")).toBe("true");

  fireEvent.keyDown(map, { key: "ArrowLeft" });
  expect(document.activeElement).toBe(ledger);
  expect(ledger.getAttribute("aria-selected")).toBe("true");

  fireEvent.keyDown(ledger, { key: "End" });
  expect(document.activeElement).toBe(ledger);
  expect(ledger.getAttribute("aria-selected")).toBe("true");
});

it("navigates back through the window caption instead of a sections toggle", () => {
  render(<SocietyView />);
  expect(screen.queryByRole("button", { name: "society.world.toggle_sections" })).toBeNull();
  expect(screen.queryByRole("button", { name: "settings_hub.back_to_app" })).toBeNull();
  // The caption sidebar toggle (owned by TopBar) is the way back to the app.
  expect(screen.getByTestId("mode-switch")).toBeTruthy();
});

it("leaves Map on Escape without requesting native fullscreen in a browser", async () => {
  render(<SocietyView />);
  fireEvent.click(screen.getByRole("tab", { name: "society.world.mode_map" }));
  await screen.findByTestId("map");
  expect(setMapFullscreen).not.toHaveBeenCalledWith(true);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByTestId("map")).toBeNull();
});

it("shows no station shortcut in Agents mode", () => {
  window.history.replaceState(null, "", "?view=agents");
  render(<SocietyView />);
  expect(screen.queryByRole("button", { name: "society.mars.station_title" })).toBeNull();
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


