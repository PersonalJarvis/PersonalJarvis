import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgenticIdeView } from "./AgenticIdeView";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeChatStore } from "@/store/ideChat";
import { balancedLayout } from "@/components/agentic/workspaceDocking";

const api = vi.hoisted(() => ({
  fetchIdeState: vi.fn(), fetchIdeProjects: vi.fn(), fetchIdeAgents: vi.fn(),
  startIdeSession: vi.fn(), activateWorkspace: vi.fn(), restoreIdeWorkspace: vi.fn(),
  addTerminal: vi.fn(), closeTerminal: vi.fn(), closeWorkspace: vi.fn(), renameWorkspace: vi.fn(), reorderIdeTerminals: vi.fn(), pushToast: vi.fn(),
  syncAgenticIdeSurface: vi.fn(() => Promise.resolve()),
}));
const openProject = vi.hoisted(() => vi.fn());
const computers = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock("@/lib/computersApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/computersApi")>();
  return { ...actual, computersApi: { ...actual.computersApi, list: computers.list } };
});
const git = vi.hoisted(() => ({ inspectGit: vi.fn(), prepareGit: vi.fn() }));
vi.mock("@/lib/gitApi", async (importOriginal) => ({ ...(await importOriginal<object>()), ...git }));
vi.mock("@/lib/agenticIdeApi", () => api);
vi.mock("@/lib/chatLibraryApi", () => ({ openProject }));
vi.mock("@/store/events", () => ({ useEventStore: (select: (value: unknown) => unknown) => select({ pushToast: api.pushToast }) }));
vi.mock("@/components/agentic/FolderPicker", () => ({ FolderPicker: ({ onSelect }: { onSelect: (path: string) => void }) => <button onClick={() => onSelect("/code/app")}>Pick folder</button> }));
vi.mock("@/components/agentic/VoiceBubble", () => ({ VoiceBubble: () => null, storedVoiceBubbleOpen: () => false, storeVoiceBubbleOpen: vi.fn() }));
vi.mock("@/components/agentic/WorkspaceTerminalGrid", () => ({ WorkspaceTerminalGrid: ({ session, onAdd }: { session: { id: string }; onAdd: () => void }) => <><div data-testid="live-grid">{session.id}</div><button onClick={onAdd}>Pane add</button></> }));

const emptyState = { active: false, session: null, max_terminals: 8, workspaces: [], active_id: null };
const project = { id: "p1", path: "/code/app", name: "App", color: null, pinned: false, archived: false,
  created_at: 0, last_opened_at: 0, exists: true, chats: 0, scratch: false, workspaces: [] };
const repoInfo = {
  folder: "/code/app", git_available: true, gh_available: false, is_repo: true, root: "/code/app", main_root: "/code/app",
  is_worktree: false, branch: "main", detached: false, unborn: false, head: "abc", upstream: "", ahead: 0, behind: 0,
  staged: 0, unstaged: 0, untracked: 0, conflicted: 0, insertions: 0, deletions: 0, dirty: false, default_branch: "main",
  remotes: [], branches: [{ name: "main", current: true, upstream: "", committed_at: 0, worktree: "/code/app" }],
  remote_branches: [], worktrees: [{ path: "/code/app", branch: "main", head: "abc", main: true, detached: false, locked: false, prunable: false, current: true }],
  changes: [], suggested_branch: "agent/brave-river-0001", worktree_dir: "/code/app/.worktrees/agent-brave-river-0001",
};
const agent = { name: "codex", display_name: "Codex", installed: true, version: "1", install_command: null };

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchIdeState.mockResolvedValue(emptyState);
  api.fetchIdeProjects.mockResolvedValue({ projects: [], active_project_id: null, active_workspace_id: null, max_terminals: 8 });
  api.fetchIdeAgents.mockResolvedValue({ terminal_available: true, max_terminals: 8, suggested_names: [], agents: [agent] });
  useIdeProjectsStore.setState({ projects: [], activeWorkspaceId: null, pendingWorkspaceId: null, refreshRequest: null, action: null });
  git.inspectGit.mockResolvedValue(repoInfo);
  computers.list.mockResolvedValue([]);
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
    await waitFor(() => expect(api.startIdeSession).toHaveBeenCalledWith("/code/app", [{ agent: "codex" }, { agent: "claude" }], { projectId: "p1", name: "Installer", onMessage: expect.any(Function) }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "New workspace" })).toBeNull());
  });

  it("opens a new workspace in a fresh git worktree", async () => {
    api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_project_id: null, active_workspace_id: null, max_terminals: 8 });
    api.startIdeSession.mockResolvedValue(emptyState);
    git.prepareGit.mockResolvedValue({ folder: "/code/app/.worktrees/agent-brave-river-0001", branch: "agent/brave-river-0001", created: true, message: "" });
    render(<AgenticIdeView />);
    await screen.findByText("Choose a workspace");
    act(() => useIdeProjectsStore.getState().newWorkspace("p1"));
    const dialog = await screen.findByRole("dialog", { name: "New workspace" });
    // Keeping the checkout is the default, so the git choices sit folded behind one summary line.
    fireEvent.click(await within(dialog).findByRole("button", { name: /Git options/ }));
    fireEvent.click(await within(dialog).findByRole("radio", { name: /New worktree/ }));
    expect((within(dialog).getByLabelText("Branch name") as HTMLInputElement).value).toBe("agent/brave-river-0001");
    fireEvent.click(screen.getByRole("button", { name: "Create workspace" }));
    await waitFor(() => expect(git.prepareGit).toHaveBeenCalledWith("/code/app", { mode: "new_worktree", branch: "agent/brave-river-0001", base: "main" }));
    expect(api.startIdeSession).toHaveBeenCalledWith("/code/app/.worktrees/agent-brave-river-0001", [{ agent: "codex" }], { projectId: "p1", name: "agent/brave-river-0001", onMessage: expect.any(Function) });
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
    await waitFor(() => expect(api.addTerminal).toHaveBeenCalledWith({ workspace_id: "w1", agent: "codex", direction: "down" }, { onMessage: expect.any(Function) }));
  });

  it("lets the user choose which pane to split and in which direction", async () => {
    const terminals = [{ key: "t1", history_id: "id1", name: "T1", display_name: "Codex" }, { key: "t2", history_id: "id2", name: "T2", display_name: "Codex" }];
    const session = { id: "w1", project_id: "p1", folder: "/code/app", name: "App work", created_at: 0,
      focus_mode: false, project: { name: "App" }, terminals, layout: balancedLayout(["t1", "t2"]) };
    api.fetchIdeState.mockResolvedValue({ ...emptyState, active: true, active_id: "w1", session });
    api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_workspace_id: "w1" });
    api.addTerminal.mockResolvedValue(session);
    render(<AgenticIdeView />);
    fireEvent.click(await screen.findByRole("button", { name: "Pane add" }));
    const dialog = screen.getByRole("dialog", { name: "Add coding agent" });
    // Still the automatic grid, so that is what the dialog offers first.
    const anchor = within(dialog).getByRole("combobox", { name: "Split next to" });
    expect(anchor.getAttribute("data-value")).toBe("");
    expect(within(dialog).queryByRole("radio", { name: "Split left" })).toBeNull();
    fireEvent.click(anchor);
    fireEvent.click(await within(dialog).findByRole("option", { name: "T2 · Codex" }));
    expect(anchor.getAttribute("data-value")).toBe("T2");
    fireEvent.click(within(dialog).getByRole("radio", { name: "Split left" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Codex" }));
    await waitFor(() => expect(api.addTerminal).toHaveBeenCalledWith({ workspace_id: "w1", agent: "codex", anchor: "T2", direction: "left" }, { onMessage: expect.any(Function) }));
    expect(api.reorderIdeTerminals).not.toHaveBeenCalled();
  });

  it("shows the pane count and refuses a new agent once the workspace is full", async () => {
    const terminals = Array.from({ length: 16 }, (_, index) => ({ key: `t${index}`, history_id: `id${index}`, name: `T${index}`, display_name: "Codex" }));
    const session = { id: "w1", project_id: "p1", folder: "/code/app", name: "App work", created_at: 0,
      focus_mode: false, project: { name: "App" }, terminals, layout: balancedLayout(terminals.map((terminal) => terminal.key)) };
    api.fetchIdeState.mockResolvedValue({ ...emptyState, active: true, active_id: "w1", session, max_terminals: 16 });
    api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_workspace_id: "w1" });
    render(<AgenticIdeView />);
    fireEvent.click(await screen.findByRole("button", { name: "Pane add" }));
    const dialog = screen.getByRole("dialog", { name: "Add coding agent" });
    expect(within(dialog).getByText(/16 of 16 agents/)).toBeTruthy();
    expect(within(dialog).getByRole("status").textContent).toContain("This workspace is full");
    expect((within(dialog).getByRole("button", { name: "Codex" }) as HTMLButtonElement).disabled).toBe(true);
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

describe("Agentic IDE on a connected computer", () => {
  const vps = { id: "c1", name: "vps", kind: "server", health: { status: "online", checked_at: 0, latency_ms: 5, message: null, load_1m: null, mem_used_pct: null } };
  const remoteSession = { id: "w1", project_id: "p1", folder: "/code/app", name: "App work", created_at: 0, focus_mode: false, project: { name: "App" },
    terminals: [{ key: "t1", history_id: "id1", name: "T1", display_name: "Codex", computer_id: "c1" }] };
  const withClaudeOnlyThere = [agent, { ...agent, name: "claude", display_name: "Claude Code", installed: false }];

  beforeEach(() => {
    computers.list.mockResolvedValue([vps]);
    // A pane jump left behind by an earlier test would switch the workspace.
    useIdeChatStore.setState({ paneRequest: null, stagedPane: null });
    try { localStorage.clear(); } catch { /* storage blocked */ }
  });

  it("opens a new agent where its neighbours run and offers the server's CLIs", async () => {
    api.fetchIdeState.mockResolvedValue({ ...emptyState, active: true, active_id: "w1", session: remoteSession });
    api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_workspace_id: "w1" });
    api.fetchIdeAgents.mockResolvedValue({ terminal_available: true, max_terminals: 8, suggested_names: [], agents: withClaudeOnlyThere });
    api.addTerminal.mockResolvedValue(remoteSession);
    render(<AgenticIdeView />);
    fireEvent.click(await screen.findByRole("button", { name: "Pane add" }));
    const dialog = screen.getByRole("dialog", { name: "Add coding agent" });
    const onVps = await within(dialog).findByRole("radio", { name: /vps/ });
    expect(onVps.getAttribute("aria-checked")).toBe("true");
    // Installed only on the server: offered there, not for this PC.
    fireEvent.click(within(dialog).getByRole("button", { name: "Claude Code" }));
    await waitFor(() => expect(api.addTerminal).toHaveBeenCalledWith(
      expect.objectContaining({ workspace_id: "w1", agent: "claude", computer_id: "c1" }), { onMessage: expect.any(Function) }));
  });

  it("says 'this PC' explicitly for a local agent in a remote workspace", async () => {
    api.fetchIdeState.mockResolvedValue({ ...emptyState, active: true, active_id: "w1", session: remoteSession });
    api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_workspace_id: "w1" });
    api.fetchIdeAgents.mockResolvedValue({ terminal_available: true, max_terminals: 8, suggested_names: [], agents: withClaudeOnlyThere });
    api.addTerminal.mockResolvedValue(remoteSession);
    render(<AgenticIdeView />);
    fireEvent.click(await screen.findByRole("button", { name: "Pane add" }));
    const dialog = screen.getByRole("dialog", { name: "Add coding agent" });
    await within(dialog).findByRole("radio", { name: /vps/ });
    fireEvent.click(within(dialog).getByRole("radio", { name: /This computer/ }));
    expect(within(dialog).queryByRole("button", { name: "Claude Code" })).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "Codex" }));
    await waitFor(() => expect(api.addTerminal).toHaveBeenCalledWith(
      expect.objectContaining({ agent: "codex", computer_id: null }), { onMessage: expect.any(Function) }));
  });

  it("creates a workspace on the server and remembers that choice for the project", async () => {
    api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_project_id: null, active_workspace_id: null, max_terminals: 8 });
    api.startIdeSession.mockResolvedValue(emptyState);
    render(<AgenticIdeView />);
    await screen.findByText("Choose a workspace");
    act(() => useIdeProjectsStore.getState().newWorkspace("p1"));
    let dialog = await screen.findByRole("dialog", { name: "New workspace" });
    fireEvent.click(await within(dialog).findByRole("radio", { name: /vps/ }));
    expect(within(dialog).getByTestId("ide-run-on-note").textContent).toContain(".env");
    fireEvent.click(within(dialog).getByRole("button", { name: "Create workspace" }));
    await waitFor(() => expect(api.startIdeSession).toHaveBeenCalledWith("/code/app", [{ agent: "codex" }],
      expect.objectContaining({ projectId: "p1", computerId: "c1" })));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "New workspace" })).toBeNull());

    act(() => useIdeProjectsStore.getState().newWorkspace("p1"));
    dialog = await screen.findByRole("dialog", { name: "New workspace" });
    expect((await within(dialog).findByRole("radio", { name: /vps/ })).getAttribute("aria-checked")).toBe("true");
  });

  it("falls back to this PC when the remembered computer is gone", async () => {
    localStorage.setItem("jarvis.agenticIde.runOn.p1", "c-removed");
    api.fetchIdeProjects.mockResolvedValue({ projects: [project], active_project_id: null, active_workspace_id: null, max_terminals: 8 });
    render(<AgenticIdeView />);
    await screen.findByText("Choose a workspace");
    act(() => useIdeProjectsStore.getState().newWorkspace("p1"));
    const dialog = await screen.findByRole("dialog", { name: "New workspace" });
    await waitFor(() => expect(within(dialog).getByRole("radio", { name: /This computer/ }).getAttribute("aria-checked")).toBe("true"));
  });
});
