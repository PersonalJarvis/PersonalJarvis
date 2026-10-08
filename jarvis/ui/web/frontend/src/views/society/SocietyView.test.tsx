import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { setMapFullscreen } from "@/lib/mapFullscreen";
import { LAST_AGENT_STORAGE_KEY } from "./lastAgent";
import { SocietyView } from "./SocietyView";

const app = vi.hoisted(() => ({ instance: { name: "default", isDev: false } }));
const groupsState = vi.hoisted(() => ({ groups: [] as Array<{ group_id: string; name: string; members: string[] }> }));
const quick = vi.hoisted(() => ({ create: vi.fn(async (): Promise<{ agentId: string }> => ({ agentId: "specialist" })) }));
vi.mock("@/hooks/useAppInstance", () => ({ useAppInstance: () => app.instance }));
beforeEach(() => {
  app.instance = { name: "default", isDev: false }; groupsState.groups = [];
  quick.create.mockReset(); quick.create.mockResolvedValue({ agentId: "specialist" });
});

vi.mock("@/lib/mapFullscreen", () => ({ setMapFullscreen: vi.fn(async () => undefined) }));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-query")>()),
  useQueryClient: () => ({ invalidateQueries: async () => undefined }),
}));
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key, useLocaleChunk: () => true }));
vi.mock("@/components/society/chat/useModelMenuData", () => ({ useModelMenuData: () => undefined }));
vi.mock("@/lib/societyChatGroups", () => ({ useSocietyChatGroups: () => ({ data: groupsState.groups }) }));
vi.mock("@/components/society/chat/ChatGroupPanel", () => ({ ChatGroupPanel: ({ group, onOpenAgent }: any) => <div data-testid="group-workspace">{group.name}<button onClick={() => onOpenAgent("specialist")}>Open member</button></div> }));
vi.mock("@/components/society/data", () => ({ useQuickCreateAgent: () => quick.create, useSocietyRoster: () => ({ data: { sample: false, agents: [
  { agentId: "lead", name: "Lead", tier: "lead", state: "idle" },
  { agentId: "specialist", name: "Specialist", tier: "specialist", state: "idle" },
] }, isLoading: false }) }));
vi.mock("@/views/JarvisAgentsView", () => ({ JarvisAgentsView: ({ active, onSelectAgent, onOpenAgents, onMarsSelectionChange }: any) => (
  <div data-testid="map" data-active={active}><button onClick={() => onSelectAgent("specialist")}>Map specialist</button><button onClick={onOpenAgents}>Map fallback</button><div data-mars-ui><input aria-label="Mars draft" /></div><div data-mars-mode="player"><button>Player viewport</button></div><div data-mars-mode="follow"><button>Follow viewport</button></div><button onClick={() => onMarsSelectionChange(false)}>Previous world</button><button onClick={() => onMarsSelectionChange(true)}>Mars world</button></div>
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
vi.mock("@/components/society/card/BuildingCardOverlay", () => ({ BuildingCardOverlay: () => null }));

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
  const map = await screen.findByTestId("map");
  expect(map.dataset.active).toBe("true");
  fireEvent.click(screen.getByRole("tab", { name: "society.roster.title" }));
  expect(screen.getByTestId("society-world-surface").getAttribute("aria-hidden")).toBe("true");
  expect(screen.getByText("Specialist")).toBeTruthy();
  expect(screen.getByLabelText("Draft")).toBe(draft);
  expect(draft.value).toBe("Unsent message");
  expect(screen.getByTestId("map")).toBe(map);
  expect(map.dataset.active).toBe("false");
  fireEvent.click(screen.getByRole("tab", { name: "society.world.mode_map" }));
  expect(screen.getByTestId("map")).toBe(map);
  expect(map.dataset.active).toBe("true");
  expect(screen.getByTestId("society-world-surface").getAttribute("aria-hidden")).toBe("false");
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
  expect(screen.getByTestId("society-world-surface").getAttribute("aria-hidden")).toBe("true");
  expect(screen.getByText("Specialist")).toBeTruthy();
  fireEvent.click(screen.getByText("Create agent"));
  await waitFor(() => expect(quick.create).toHaveBeenCalledTimes(1));
});

it("one click creates an agent and opens its chat", async () => {
  render(<SocietyView />);
  expect(screen.getByText("Lead")).toBeTruthy();
  fireEvent.click(screen.getByText("Create agent"));
  expect(await screen.findByText("Specialist")).toBeTruthy();
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("shows a failed one-click creation instead of inventing a row", async () => {
  quick.create.mockRejectedValueOnce(new Error("no seat connected"));
  render(<SocietyView />);
  fireEvent.click(screen.getByText("Create agent"));
  expect((await screen.findByRole("alert")).textContent).toBe("no seat connected");
  expect(screen.getByText("Lead")).toBeTruthy();
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
  expect(screen.getByTestId("society-world-surface").getAttribute("aria-hidden")).toBe("true");
  expect(screen.getByTestId("mode-switch")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "settings_hub.back_to_app" })).toBeNull();
});

it("navigates back through the window caption instead of a sections toggle", () => {
  render(<SocietyView />);
  expect(screen.queryByRole("button", { name: "society.world.toggle_sections" })).toBeNull();
  expect(screen.queryByRole("button", { name: "settings_hub.back_to_app" })).toBeNull();
  // The caption back button leaves this fullscreen section. The sidebar toggle is gone.
  expect(screen.getByTestId("mode-switch")).toBeTruthy();
});

it("leaves Map on Escape without requesting native fullscreen in a browser", async () => {
  render(<SocietyView />);
  fireEvent.click(screen.getByRole("tab", { name: "society.world.mode_map" }));
  await screen.findByTestId("map");
  expect(setMapFullscreen).not.toHaveBeenCalledWith(true);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.getByTestId("society-world-surface").getAttribute("aria-hidden")).toBe("true");
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
  expect(screen.getByTestId("society-world-surface").getAttribute("aria-hidden")).toBe("true");
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
  expect(screen.getByTestId("society-world-surface").getAttribute("aria-hidden")).toBe("true");
});

it("still leaves the ordinary map when browser fullscreen exits", async () => {
  render(<SocietyView />);
  fireEvent.click(screen.getByRole("tab", { name: "society.world.mode_map" }));
  await screen.findByTestId("map");
  fireEvent(document, new Event("fullscreenchange"));
  expect(screen.getByTestId("society-world-surface").getAttribute("aria-hidden")).toBe("true");
});


