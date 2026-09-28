import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgenticIdeView } from "./AgenticIdeView";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeChatStore } from "@/store/ideChat";
import { balancedLayout } from "@/components/agentic/workspaceDocking";

const api = vi.hoisted(() => ({
  fetchIdeState: vi.fn(), fetchIdeProjects: vi.fn(), fetchIdeAgents: vi.fn(),
  startIdeSession: vi.fn(), activateWorkspace: vi.fn(), restoreIdeWorkspace: vi.fn(),
  addTerminal: vi.fn(), closeTerminal: vi.fn(), closeWorkspace: vi.fn(), renameWorkspace: vi.fn(), reorderIdeTerminals: vi.fn(), pushToast: vi.fn(),
}));
const openProject = vi.hoisted(() => vi.fn());
vi.mock("@/lib/agenticIdeApi", () => api);
vi.mock("@/lib/chatLibraryApi", () => ({ openProject }));
vi.mock("@/store/events", () => ({ useEventStore: (select: (value: unknown) => unknown) => select({ pushToast: api.pushToast }) }));
vi.mock("@/components/agentic/FolderPicker", () => ({ FolderPicker: ({ onSelect }: { onSelect: (path: string) => void }) => <button onClick={() => onSelect("/code/app")}>Pick folder</button> }));
vi.mock("@/components/agentic/VoiceBubble", () => ({ VoiceBubble: () => null, storedVoiceBubbleOpen: () => false, storeVoiceBubbleOpen: vi.fn() }));
vi.mock("@/components/agentic/WorkspaceTerminalGrid", () => ({ WorkspaceTerminalGrid: ({ session, onAdd }: { session: { id: string }; onAdd: () => void }) => <><div data-testid="live-grid">{session.id}</div><button onClick={onAdd}>Pane add</button></> }));

const emptyState = { active: false, session: null, max_terminals: 8, workspaces: [], active_id: null };
const project = { id: "p1", path: "/code/app", name: "App", color: null, pinned: false, archived: false,
  created_at: 0, last_opened_at: 0, exists: true, chats: 0, scratch: false, workspaces: [] };
const agent = { name: "codex", display_name: "Codex", installed: true, version: "1", install_command: null };

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchIdeState.mockResolvedValue(emptyState);
  api.fetchIdeProjects.mockResolvedValue({ projects: [], active_project_id: null, active_workspace_id: null, max_terminals: 8 });
  api.fetchIdeAgents.mockResolvedValue({ terminal_available: true, max_terminals: 8, suggested_names: [], agents: [agent] });
  useIdeProjectsStore.setState({ projects: [], activeWorkspaceId: null, pendingWorkspaceId: null, refreshRequest: null, action: null });
});
afterEach(cleanup);

describe("Agentic IDE project flow", () => {
  it("connects a folder as a project without opening a coding session", async () => {
    openProject.mockResolvedValue(project);
    api.fetchIdeProjects.mockResolvedValueOnce({ projects: [], active_project_id: null, active_workspace_id: null, max_terminals: 8 })
      .mockResolvedValue({ projects: [project], active_project_id: null, active_workspace_id: null, max_terminals: 8 });
    render(<AgenticIdeView />);
    fireEvent.click(await screen.findByRole("button", { name: "Connect folder" }));
    expect(api.fetchIdeAgents).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "Pick folder" }));
    fireEvent.click(screen.getByRole("button", { name: "Connect project" }));
    await waitFor(() => expect(openProject).toHaveBeenCalledWith("/code/app", undefined));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Connect project" })).toBeNull());
    expect(api.startIdeSession).not.toHaveBeenCalled();
  });

  it("creates a workspace with an explicit project and per-session agents", async () => {
    api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_project_id: null, active_workspace_id: null, max_terminals: 8 });
    api.fetchIdeAgents.mockResolvedValue({ terminal_available: true, max_terminals: 8, suggested_names: [], agents: [agent,
      { ...agent, name: "claude", display_name: "Claude Code" },
      { ...agent, name: "harness", display_name: "Browser Harness", accepts_prompts: false }] });
    api.startIdeSession.mockResolvedValue(emptyState);
    render(<AgenticIdeView />);
    await screen.findByText("Choose a workspace");
    act(() => useIdeProjectsStore.getState().newWorkspace("p1"));
    await screen.findByRole("dialog", { name: "New workspace" });
    expect(screen.queryByRole("button", { name: "Browser Harness" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "2 sessions" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit session 2: Codex" }));
    fireEvent.click(screen.getByRole("button", { name: "Claude Code" }));
    fireEvent.change(screen.getByLabelText("Name (optional)"), { target: { value: "Installer" } });
    fireEvent.click(screen.getByRole("button", { name: "Create workspace" }));
    await waitFor(() => expect(api.startIdeSession).toHaveBeenCalledWith("/code/app", [{ agent: "codex" }, { agent: "claude" }], { projectId: "p1", name: "Installer" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "New workspace" })).toBeNull());
  });

  it("keeps the launch dialog open while creation is in flight", async () => {
    api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_project_id: null, active_workspace_id: null, max_terminals: 8 });
    let finish!: (state: typeof emptyState) => void;
    api.startIdeSession.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    render(<AgenticIdeView />);
    await screen.findByText("Choose a workspace");
    act(() => useIdeProjectsStore.getState().newWorkspace("p1"));
    fireEvent.click(await screen.findByRole("button", { name: "Create workspace" }));
    await screen.findByRole("button", { name: "Starting…" });
    fireEvent.keyDown(document, { key: "Escape" });
    const dialog = screen.getByRole("dialog", { name: "New workspace" });
    fireEvent.mouseDown(dialog.parentElement!);
    expect(screen.getByRole("dialog", { name: "New workspace" })).toBe(dialog);
    expect((screen.getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Close" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => finish(emptyState));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "New workspace" })).toBeNull());
  });

  it("restores a closed workspace by ID before showing its sessions", async () => {
    const workspace = { id: "w1", project_id: "p1", folder: "/code/app", name: "Installer", branch: null, terminals: 1,
      live_terminals: 0, focus_mode: false, created_at: 0, last_active_at: 0, active: false, status: "closed", restorable: true };
    api.fetchIdeProjects.mockResolvedValue({ projects: [{ ...project, workspaces: [workspace] }], active_project_id: null, active_workspace_id: null, max_terminals: 8 });
    const restored = { ...emptyState, active: true, active_id: "w1", session: {
      id: "w1", project_id: "p1", folder: "/code/app", name: "Installer", created_at: 0, focus_mode: false, project: { name: "App" }, terminals: [],
    } };
    api.restoreIdeWorkspace.mockImplementation(async () => {
      api.fetchIdeState.mockResolvedValue(restored);
      api.fetchIdeProjects.mockResolvedValue({ projects: [{ ...project, workspaces: [{ ...workspace, status: "open", active: true }] }], active_project_id: "p1", active_workspace_id: "w1", max_terminals: 8 });
      return restored;
    });
    render(<AgenticIdeView />);
    await screen.findByText("Choose a workspace");
    act(() => useIdeProjectsStore.getState().activateWorkspace("w1"));
    await waitFor(() => expect(api.restoreIdeWorkspace).toHaveBeenCalledWith("w1"));
    expect((await screen.findByTestId("live-grid")).textContent).toBe("w1");
    expect(api.activateWorkspace).not.toHaveBeenCalled();
  });

  it("shows add-agent choices in a dialog and pins the new session to its workspace", async () => {
    const session = { id: "w1", project_id: "p1", folder: "/code/app", name: "App work", created_at: 0,
      focus_mode: false, project: { name: "App" }, terminals: [] };
    const current = { ...emptyState, active: true, active_id: "w1", session };
    api.fetchIdeState.mockResolvedValue(current);
    api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_workspace_id: "w1" });
    api.addTerminal.mockResolvedValue(session);
    render(<AgenticIdeView />);
    fireEvent.click(await screen.findByRole("button", { name: "Pane add" }));
    expect(screen.getByRole("dialog", { name: "Add coding agent" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Codex" }));
    await waitFor(() => expect(api.addTerminal).toHaveBeenCalledWith({ workspace_id: "w1", agent: "codex", direction: "down" }));
  });

  it("exposes compact workspace rename and close actions", async () => {
    const session = { id: "w1", project_id: "p1", folder: "/code/app", name: "App work", created_at: 0,
      focus_mode: false, project: { name: "App" }, terminals: [] };
    const current = { ...emptyState, active: true, active_id: "w1", session };
    api.fetchIdeState.mockResolvedValue(current);
    api.fetchIdeProjects.mockResolvedValue({ projects: [{ ...project, workspaces: [{
      id: "w1", project_id: "p1", folder: "/code/app", name: "App work", branch: null, terminals: 0,
      live_terminals: 0, focus_mode: false, created_at: 0, last_active_at: 0, active: true, status: "open", restorable: true,
    }] }], active_project_id: "p1", active_workspace_id: "w1", max_terminals: 8 });
    api.renameWorkspace.mockResolvedValue(current);
    render(<AgenticIdeView />);
    await screen.findByTestId("live-grid");
    expect(screen.queryByTestId("workspace-toolbar")).toBeNull();
    act(() => useIdeProjectsStore.getState().openWorkspaceOptions("w1"));
    fireEvent.click(screen.getByRole("button", { name: "Rename workspace" }));
    fireEvent.change(screen.getByLabelText("Workspace name"), { target: { value: "Installer" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.renameWorkspace).toHaveBeenCalledWith("w1", "Installer"));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Rename workspace" })).toBeNull());
  });

  it("keeps six incrementally added agents in a balanced 3 by 2 layout", async () => {
    const terminals = Array.from({ length: 6 }, (_, index) => ({ key: `t${index}`, history_id: `id${index}`, name: `T${index}` }));
    const session = { id: "w1", project_id: "p1", folder: "/code/app", name: "App work", created_at: 0,
      focus_mode: false, project: { name: "App" }, terminals: terminals.slice(0, 5), layout: balancedLayout(terminals.slice(0, 5).map((terminal) => terminal.key)) };
    const current = { ...emptyState, active: true, active_id: "w1", session };
    api.fetchIdeState.mockResolvedValue(current);
    api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_workspace_id: "w1" });
    api.addTerminal.mockResolvedValue({ ...session, terminals, layout: { direction: "row", children: [session.layout, { pane: "t5" }], weights: [3, 1] } });
    api.reorderIdeTerminals.mockResolvedValue({ ...current, session: { ...session, terminals, layout: balancedLayout(terminals.map((terminal) => terminal.key)) } });
    render(<AgenticIdeView />);
    fireEvent.click(await screen.findByRole("button", { name: "Pane add" }));
    fireEvent.click(screen.getByRole("button", { name: "Codex" }));
    await waitFor(() => expect(api.reorderIdeTerminals).toHaveBeenCalledWith("w1", terminals.map((terminal) => terminal.history_id)));
  });

  it("persists a balanced correction for an oversized restored layout", async () => {
    const terminals = Array.from({ length: 6 }, (_, index) => ({ key: `t${index}`, history_id: `id${index}`, name: `T${index}` }));
    const session = { id: "w1", project_id: "p1", folder: "/code/app", name: "Restored", created_at: 0,
      focus_mode: false, project: { name: "App" }, terminals, layout: { direction: "row", children: terminals.map((terminal) => ({ pane: terminal.key })), weights: terminals.map(() => 1) } };
    const current = { ...emptyState, active: true, active_id: "w1", session };
    api.fetchIdeState.mockResolvedValue(current);
    api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_workspace_id: "w1" });
    api.reorderIdeTerminals.mockResolvedValue({ ...current, session: { ...session, layout: balancedLayout(terminals.map((terminal) => terminal.key)) } });
    render(<AgenticIdeView onScreen={false} />);
    await screen.findByTestId("live-grid");
    expect(api.reorderIdeTerminals).toHaveBeenCalledOnce();
    expect(api.reorderIdeTerminals).toHaveBeenCalledWith("w1", terminals.map((terminal) => terminal.history_id));
  });

  it("never rebalances a different workspace returned by a delayed add", async () => {
    const terminals = ["a", "b"].map((key) => ({ key, history_id: key, name: key }));
    const session = { id: "w1", project_id: "p1", folder: "/code/app", name: "A", created_at: 0,
      focus_mode: false, project: { name: "App" }, terminals, layout: balancedLayout(["a", "b"]) };
    const current = { ...emptyState, active: true, active_id: "w1", session };
    const otherSession = { ...session, id: "w2", name: "B", layout: { direction: "column", children: [{ pane: "a" }, { pane: "b" }], weights: [1, 1] } };
    const other = { ...current, active_id: "w2", session: otherSession };
    api.fetchIdeState.mockResolvedValue(current);
    api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_workspace_id: "w1" });
    let finishAdd!: (value: unknown) => void;
    api.addTerminal.mockImplementation(() => new Promise((resolve) => { finishAdd = resolve; }));
    api.activateWorkspace.mockImplementation(async () => {
      api.fetchIdeState.mockResolvedValue(other);
      api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_workspace_id: "w2" });
      return other;
    });
    render(<AgenticIdeView onScreen={false} />);
    fireEvent.click(await screen.findByRole("button", { name: "Pane add" }));
    fireEvent.click(screen.getByRole("button", { name: "Codex" }));
    await waitFor(() => expect(api.addTerminal).toHaveBeenCalled());
    act(() => useIdeProjectsStore.getState().activateWorkspace("w2"));
    await waitFor(() => expect(screen.getByTestId("live-grid").textContent).toBe("w2"));
    await act(async () => finishAdd(otherSession));
    expect(api.reorderIdeTerminals).not.toHaveBeenCalled();
    expect(screen.getByTestId("live-grid").textContent).toBe("w2");
  });

  it("focuses the requested pane from the sidebar agents list", async () => {
    const session = { id: "w1", project_id: "p1", folder: "/code/app", name: "A", created_at: 0,
      focus_mode: false, project: { name: "App" }, terminals: [{ key: "a", history_id: "a", name: "T1" }], layout: balancedLayout(["a"]) };
    const current = { ...emptyState, active: true, active_id: "w1", session };
    const otherSession = { ...session, id: "w2", name: "B" };
    const other = { ...current, active_id: "w2", session: otherSession };
    api.fetchIdeState.mockResolvedValue(current);
    api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_workspace_id: "w1" });
    api.activateWorkspace.mockImplementation(async () => {
      api.fetchIdeState.mockResolvedValue(other);
      api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_workspace_id: "w2" });
      return other;
    });
    useIdeChatStore.setState({ paneRequest: null, stagedPane: null });
    render(<AgenticIdeView onScreen={false} />);
    await screen.findByTestId("live-grid");
    act(() => useIdeChatStore.getState().requestPane("w2", "T1"));
    await waitFor(() => expect(api.activateWorkspace).toHaveBeenCalledWith("w2"));
    await waitFor(() => expect(useIdeChatStore.getState().stagedPane).toBe("T1"));
  });

});
