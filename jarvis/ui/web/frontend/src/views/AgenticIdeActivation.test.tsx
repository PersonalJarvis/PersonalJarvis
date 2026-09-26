import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { AgenticIdeView } from "./AgenticIdeView";

const api = vi.hoisted(() => ({
  fetchIdeState: vi.fn(), fetchIdeProjects: vi.fn(), fetchIdeAgents: vi.fn(),
  activateWorkspace: vi.fn(), restoreIdeWorkspace: vi.fn(),
}));
vi.mock("@/lib/agenticIdeApi", () => api);
vi.mock("@/lib/chatLibraryApi", () => ({ openProject: vi.fn() }));
vi.mock("@/store/events", () => ({ useEventStore: (select: (value: unknown) => unknown) => select({ pushToast: vi.fn() }) }));
vi.mock("@/components/agentic/FolderPicker", () => ({ FolderPicker: () => null }));
vi.mock("@/components/agentic/VoiceBubble", () => ({ VoiceBubble: () => null, storedVoiceBubbleOpen: () => false, storeVoiceBubbleOpen: vi.fn() }));
vi.mock("@/components/agentic/WorkspaceTerminalGrid", () => ({ WorkspaceTerminalGrid: ({ session }: { session: { id: string } }) => <div data-testid="live-grid">{session.id}</div> }));

afterEach(cleanup);

it("serializes rapid workspace switches and displays only the latest result", async () => {
  const card = (id: string) => ({ id, project_id: "p1", folder: "/code/app", name: id, branch: null,
    terminals: 1, live_terminals: 1, focus_mode: false, created_at: 0, last_active_at: 0, active: false,
    status: "open", restorable: true });
  const stateFor = (id: string) => ({ active: true, active_id: id, max_terminals: 8, workspaces: [], session: {
    id, project_id: "p1", folder: "/code/app", name: id, created_at: 0, focus_mode: false,
    project: { name: "App" }, terminals: [],
  } });
  let current: Record<string, unknown> = { active: false, active_id: null, session: null, workspaces: [], max_terminals: 8 };
  const project = { id: "p1", path: "/code/app", name: "App", archived: false, scratch: false, workspaces: [card("A"), card("B")] };
  api.fetchIdeState.mockImplementation(() => Promise.resolve(current));
  api.fetchIdeProjects.mockImplementation(() => Promise.resolve({ projects: [project],
    active_project_id: "p1", active_workspace_id: current.active_id, max_terminals: 8 }));
  api.fetchIdeAgents.mockResolvedValue({ terminal_available: true, max_terminals: 8, agents: [] });
  useIdeProjectsStore.setState({ projects: [], activeWorkspaceId: null, action: null });
  let resolveA!: (value: unknown) => void;
  let resolveB!: (value: unknown) => void;
  api.activateWorkspace.mockImplementation((id: string) => id === "A"
    ? new Promise((resolve) => { resolveA = resolve; })
    : new Promise((resolve) => { resolveB = resolve; }));

  render(<AgenticIdeView />);
  await screen.findByText("Choose a workspace");
  act(() => useIdeProjectsStore.getState().activateWorkspace("A"));
  await waitFor(() => expect(api.activateWorkspace).toHaveBeenCalledWith("A"));
  act(() => useIdeProjectsStore.getState().activateWorkspace("B"));
  expect(api.activateWorkspace).toHaveBeenCalledTimes(1);
  act(() => resolveA(stateFor("A")));
  await waitFor(() => expect(api.activateWorkspace).toHaveBeenCalledWith("B"));
  expect(screen.queryByTestId("live-grid")).toBeNull();
  current = stateFor("B");
  act(() => resolveB(current));
  expect((await screen.findByTestId("live-grid")).textContent).toBe("B");
});
