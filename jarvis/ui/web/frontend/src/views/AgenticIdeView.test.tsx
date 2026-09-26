import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgenticIdeView } from "./AgenticIdeView";
import { useIdeProjectsStore } from "@/store/ideProjects";

const api = vi.hoisted(() => ({
  fetchIdeState: vi.fn(), fetchIdeProjects: vi.fn(), fetchIdeAgents: vi.fn(),
  startIdeSession: vi.fn(), activateWorkspace: vi.fn(), restoreIdeWorkspace: vi.fn(),
  addTerminal: vi.fn(), closeTerminal: vi.fn(), closeWorkspace: vi.fn(), renameWorkspace: vi.fn(),
}));
const openProject = vi.hoisted(() => vi.fn());
vi.mock("@/lib/agenticIdeApi", () => api);
vi.mock("@/lib/chatLibraryApi", () => ({ openProject }));
vi.mock("@/store/events", () => ({ useEventStore: (select: (value: unknown) => unknown) => select({ pushToast: vi.fn() }) }));
vi.mock("@/components/agentic/FolderPicker", () => ({ FolderPicker: ({ onSelect }: { onSelect: (path: string) => void }) => <button onClick={() => onSelect("/code/app")}>Pick folder</button> }));
vi.mock("@/components/agentic/VoiceBubble", () => ({ VoiceBubble: () => null, storedVoiceBubbleOpen: () => false, storeVoiceBubbleOpen: vi.fn() }));
vi.mock("@/components/agentic/WorkspaceTerminalGrid", () => ({ WorkspaceTerminalGrid: ({ session }: { session: { id: string } }) => <div data-testid="live-grid">{session.id}</div> }));

const emptyState = { active: false, session: null, max_terminals: 8, workspaces: [], active_id: null };
const project = { id: "p1", path: "/code/app", name: "App", color: null, pinned: false, archived: false,
  created_at: 0, last_opened_at: 0, exists: true, chats: 0, scratch: false, workspaces: [] };
const agent = { name: "codex", display_name: "Codex", installed: true, version: "1", install_command: null };

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchIdeState.mockResolvedValue(emptyState);
  api.fetchIdeProjects.mockResolvedValue({ projects: [], active_project_id: null, active_workspace_id: null, max_terminals: 8 });
  api.fetchIdeAgents.mockResolvedValue({ terminal_available: true, max_terminals: 8, suggested_names: [], agents: [agent] });
  useIdeProjectsStore.setState({ projects: [], activeWorkspaceId: null, action: null });
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
      { ...agent, name: "harness", display_name: "Browser Harness", accepts_prompts: false }] });
    api.startIdeSession.mockResolvedValue(emptyState);
    render(<AgenticIdeView />);
    await screen.findByText("Choose a workspace");
    act(() => useIdeProjectsStore.getState().newWorkspace("p1"));
    await screen.findByRole("dialog", { name: "New workspace" });
    expect(screen.queryByRole("option", { name: "Browser Harness" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Sessions"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Name (optional)"), { target: { value: "Installer" } });
    fireEvent.click(screen.getByRole("button", { name: "Create workspace" }));
    await waitFor(() => expect(api.startIdeSession).toHaveBeenCalledWith("/code/app", [{ agent: "codex" }, { agent: "codex" }], { projectId: "p1", name: "Installer" }));
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
    fireEvent.click(await screen.findByRole("button", { name: "Workspace actions" }));
    fireEvent.click(screen.getByRole("button", { name: "Rename workspace" }));
    fireEvent.change(screen.getByLabelText("Workspace name"), { target: { value: "Installer" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.renameWorkspace).toHaveBeenCalledWith("w1", "Installer"));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Rename workspace" })).toBeNull());
  });

});
