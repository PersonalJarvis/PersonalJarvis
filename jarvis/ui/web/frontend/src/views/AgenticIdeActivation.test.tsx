import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { AgenticIdeView } from "./AgenticIdeView";

const api = vi.hoisted(() => ({
  fetchIdeState: vi.fn(), fetchIdeProjects: vi.fn(), fetchIdeAgents: vi.fn(),
  activateWorkspace: vi.fn(), restoreIdeWorkspace: vi.fn(),
  pushToast: vi.fn(),
}));
vi.mock("@/lib/agenticIdeApi", () => api);
vi.mock("@/lib/chatLibraryApi", () => ({ openProject: vi.fn() }));
vi.mock("@/store/events", () => ({ useEventStore: (select: (value: unknown) => unknown) => select({ pushToast: api.pushToast }) }));
vi.mock("@/components/agentic/FolderPicker", () => ({ FolderPicker: () => null }));
vi.mock("@/components/agentic/VoiceBubble", () => ({ VoiceBubble: () => null, storedVoiceBubbleOpen: () => false, storeVoiceBubbleOpen: vi.fn() }));
vi.mock("@/components/agentic/WorkspaceTerminalGrid", () => ({ WorkspaceTerminalGrid: ({ session }: { session: { id: string } }) => <div data-testid="live-grid">{session.id}</div> }));

afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

function deferred() {
  let resolve!: (value: unknown) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function setupRecovery() {
  const stateFor = (id: string) => ({ active: true, active_id: id, max_terminals: 8, workspaces: [], session: {
    id, project_id: "p1", folder: "/code/app", name: id, created_at: 0, focus_mode: false,
    project: { name: "App" }, terminals: [],
  } });
  let current = stateFor("Previous");
  const project = { id: "p1", path: "/code/app", name: "App", archived: false, scratch: false,
    workspaces: ["Previous", "A", "B", "C"].map((id) => ({ id, name: id, project_id: "p1", folder: "/code/app", status: "open" })) };
  api.fetchIdeState.mockImplementation(() => Promise.resolve(current));
  api.fetchIdeProjects.mockImplementation(() => Promise.resolve({ projects: [project], active_project_id: "p1",
    active_workspace_id: current.active_id, max_terminals: 8 }));
  api.fetchIdeAgents.mockResolvedValue({ terminal_available: true, max_terminals: 8, agents: [] });
  useIdeProjectsStore.setState({ projects: [], activeWorkspaceId: null, pendingWorkspaceId: null, refreshRequest: null, action: null });
  const activations = { A: deferred(), B: deferred(), C: deferred() };
  api.activateWorkspace.mockImplementation((id: keyof typeof activations) => activations[id].promise);
  const setCurrent = (id: string) => { current = stateFor(id); return current; };
  return { activations, setCurrent };
}

it("reconciles a superseded successful activation immediately when the latest switch fails", async () => {
  const { activations, setCurrent } = setupRecovery();
  render(<AgenticIdeView onScreen={false} />);
  expect((await screen.findByTestId("live-grid")).textContent).toBe("Previous");
  act(() => useIdeProjectsStore.getState().activateWorkspace("A"));
  await waitFor(() => expect(api.activateWorkspace).toHaveBeenCalledWith("A"));
  act(() => useIdeProjectsStore.getState().activateWorkspace("B"));
  act(() => activations.A.resolve(setCurrent("A")));
  await waitFor(() => expect(api.activateWorkspace).toHaveBeenCalledWith("B"));
  const recovery = deferred();
  api.fetchIdeState.mockImplementationOnce(() => recovery.promise);
  const reads = api.fetchIdeState.mock.calls.length;
  act(() => activations.B.reject(new Error("Workspace B is unavailable")));
  await waitFor(() => expect(api.fetchIdeState).toHaveBeenCalledTimes(reads + 1));
  expect(useIdeProjectsStore.getState().pendingWorkspaceId).toBe("B");
  expect(screen.getByTestId("live-grid").textContent).toBe("Previous");
  act(() => recovery.resolve(setCurrent("A")));
  await waitFor(() => expect(screen.getByTestId("live-grid").textContent).toBe("A"));
  expect(useIdeProjectsStore.getState().activeWorkspaceId).toBe("A");
  expect(useIdeProjectsStore.getState().pendingWorkspaceId).toBeNull();
  expect(api.activateWorkspace.mock.calls.map(([id]) => id)).toEqual(["A", "B"]);
  expect(api.pushToast).toHaveBeenCalledWith("error", "Workspace B is unavailable");
});

it("preserves a newer workspace click while failure recovery is reading the backend", async () => {
  const { activations, setCurrent } = setupRecovery();
  render(<AgenticIdeView onScreen={false} />);
  await screen.findByTestId("live-grid");
  act(() => useIdeProjectsStore.getState().activateWorkspace("A"));
  await waitFor(() => expect(api.activateWorkspace).toHaveBeenCalledWith("A"));
  act(() => useIdeProjectsStore.getState().activateWorkspace("B"));
  act(() => activations.A.resolve(setCurrent("A")));
  await waitFor(() => expect(api.activateWorkspace).toHaveBeenCalledWith("B"));
  const recovery = deferred();
  api.fetchIdeState.mockImplementationOnce(() => recovery.promise);
  const reads = api.fetchIdeState.mock.calls.length;
  act(() => activations.B.reject(new Error("Workspace B is unavailable")));
  await waitFor(() => expect(api.fetchIdeState).toHaveBeenCalledTimes(reads + 1));
  act(() => useIdeProjectsStore.getState().activateWorkspace("C"));
  expect(useIdeProjectsStore.getState().pendingWorkspaceId).toBe("C");
  expect(api.activateWorkspace.mock.calls.map(([id]) => id)).toEqual(["A", "B"]);
  act(() => recovery.resolve(setCurrent("A")));
  await waitFor(() => expect(api.activateWorkspace).toHaveBeenCalledWith("C"));
  expect(screen.getByTestId("live-grid").textContent).toBe("Previous");
  expect(useIdeProjectsStore.getState().pendingWorkspaceId).toBe("C");
  act(() => activations.C.resolve(setCurrent("C")));
  await waitFor(() => expect(screen.getByTestId("live-grid").textContent).toBe("C"));
  expect(useIdeProjectsStore.getState().activeWorkspaceId).toBe("C");
  expect(useIdeProjectsStore.getState().pendingWorkspaceId).toBeNull();
  expect(api.activateWorkspace.mock.calls.map(([id]) => id)).toEqual(["A", "B", "C"]);
});

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
  useIdeProjectsStore.setState({ projects: [], activeWorkspaceId: null, pendingWorkspaceId: null, refreshRequest: null, action: null });
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
