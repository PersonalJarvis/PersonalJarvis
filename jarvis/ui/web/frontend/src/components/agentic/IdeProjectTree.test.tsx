import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { IdeProjectTree } from "./IdeProjectTree";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { IdeApiError, type IdeProject } from "@/lib/agenticIdeApi";
import { ChatLibraryError } from "@/lib/chatLibraryApi";

const patchProject = vi.hoisted(() => vi.fn());
const openProject = vi.hoisted(() => vi.fn());
const deleteProject = vi.hoisted(() => vi.fn());
const reorderProjects = vi.hoisted(() => vi.fn());
const revealProject = vi.hoisted(() => vi.fn());
const fetchProjectLaunchers = vi.hoisted(() => vi.fn());
const openProjectIn = vi.hoisted(() => vi.fn());
const robustCopy = vi.hoisted(() => vi.fn());
vi.mock("@/lib/clipboard", () => ({ robustCopy }));
vi.mock("@/lib/chatLibraryApi", () => ({ patchProject, openProject, deleteProject, reorderProjects, revealProject, fetchProjectLaunchers, openProjectIn,
  ChatLibraryError: class extends Error { constructor(message: string, readonly status: number) { super(message); } },
}));

const renameWorkspace = vi.hoisted(() => vi.fn());
const closeWorkspace = vi.hoisted(() => vi.fn());
const removeWorkspace = vi.hoisted(() => vi.fn());
const reorderWorkspaces = vi.hoisted(() => vi.fn());
const addTerminal = vi.hoisted(() => vi.fn());
const fetchWorkspacePanes = vi.hoisted(() => vi.fn());
const interruptTerminal = vi.hoisted(() => vi.fn());
const startIdeSession = vi.hoisted(() => vi.fn());
vi.mock("@/lib/agenticIdeApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/agenticIdeApi")>();
  return { ...actual, renameWorkspace, closeWorkspace, removeWorkspace, reorderWorkspaces, addTerminal, fetchWorkspacePanes, interruptTerminal, startIdeSession };
});

const project = (id = "p1", pinned = false) => ({ id, path: `/${id}`, name: id === "p1" ? "App" : "New App",
  color: null, pinned, archived: false, scratch: false, created_at: 0, last_opened_at: 0, exists: true, chats: 0,
  workspaces: [{ id: `${id}-w1`, name: "Work", status: "open", live_terminals: 1, terminals: 1, restorable: true }] }) as IdeProject;

beforeEach(() => {
  localStorage.clear();
  patchProject.mockReset().mockResolvedValue({});
  openProject.mockReset().mockResolvedValue({ id: "p1" });
  deleteProject.mockReset().mockResolvedValue(true);
  reorderProjects.mockReset().mockResolvedValue([]);
  revealProject.mockReset().mockResolvedValue(true);
  fetchProjectLaunchers.mockReset().mockResolvedValue({
    file_manager: true, editors: [{ id: "code", label: "VS Code" }], remote_url: "https://github.com/me/app", remote_label: "GitHub",
  });
  openProjectIn.mockReset().mockResolvedValue(true);
  addTerminal.mockReset().mockResolvedValue({});
  fetchWorkspacePanes.mockReset().mockResolvedValue({ active_id: "p1-w1", panes: [
    { workspace_id: "p1-w1", key: "T1", agent: "claude", account: null, activity: "working" },
    { workspace_id: "p1-w1", key: "T2", agent: "codex", account: "work", activity: "waiting" },
    { workspace_id: "other", key: "T1", agent: "claude", account: null, activity: "working" },
  ] });
  interruptTerminal.mockReset().mockResolvedValue(undefined);
  startIdeSession.mockReset().mockResolvedValue({});
  robustCopy.mockReset().mockResolvedValue(true);
  renameWorkspace.mockReset().mockResolvedValue({});
  closeWorkspace.mockReset().mockResolvedValue({});
  removeWorkspace.mockReset().mockResolvedValue({});
  reorderWorkspaces.mockReset().mockResolvedValue({});
  useIdeProjectsStore.setState({ projects: [project()], activeWorkspaceId: "p1-w1", pendingWorkspaceId: null, refreshRequest: null, action: null });
});
afterEach(cleanup);

it("keeps a manual collapse across polling and remount", () => {
  const { unmount } = render(<IdeProjectTree />);
  const selected = screen.getByTestId("ide-workspace-p1-w1");
  expect(selected.parentElement?.className).toContain("bg-muted");
  expect(selected.className).toContain("min-h-8");
  expect(screen.getByTestId("ide-project-p1").firstElementChild?.className).toContain("min-h-8");
  fireEvent.click(screen.getByRole("button", { name: "Collapse App" }));
  expect(screen.queryByTestId("ide-workspace-p1-w1")).toBeNull();
  act(() => useIdeProjectsStore.getState().publish([{ ...project() }], "p1-w1"));
  expect(screen.queryByTestId("ide-workspace-p1-w1")).toBeNull();
  unmount();
  render(<IdeProjectTree />);
  expect(screen.queryByTestId("ide-workspace-p1-w1")).toBeNull();
});

it("expands the same active workspace after a close and reopen", () => {
  render(<IdeProjectTree />);
  fireEvent.click(screen.getByRole("button", { name: "Collapse App" }));
  act(() => useIdeProjectsStore.getState().publish([project()], null));
  act(() => useIdeProjectsStore.getState().publish([project()], "p1-w1"));
  expect(screen.getByTestId("ide-workspace-p1-w1")).toBeDefined();
});

it("opens the first new project and keeps its plus independent", () => {
  useIdeProjectsStore.setState({ projects: [project()], activeWorkspaceId: null });
  render(<IdeProjectTree />);
  expect(screen.getByTestId("ide-workspace-p1-w1")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Collapse App" }));
  fireEvent.click(screen.getByRole("button", { name: "New workspace in App" }));
  expect(screen.queryByTestId("ide-workspace-p1-w1")).toBeNull();
  expect(useIdeProjectsStore.getState().action?.kind).toBe("new-workspace");
  fireEvent.click(screen.getByRole("button", { name: "Connect project" }));
  expect(useIdeProjectsStore.getState().action?.kind).toBe("connect-project");
});

it("expands a manually collapsed project when its first workspace becomes active", () => {
  useIdeProjectsStore.setState({ projects: [project()], activeWorkspaceId: null });
  render(<IdeProjectTree />);
  fireEvent.click(screen.getByRole("button", { name: "Collapse App" }));
  expect(screen.queryByTestId("ide-workspace-p1-w1")).toBeNull();
  act(() => useIdeProjectsStore.getState().publish([project()], "p1-w1"));
  expect(screen.getByTestId("ide-workspace-p1-w1")).toBeDefined();
});

it("counts agent sessions across a project's workspaces", () => {
  const withTwo = { ...project(), workspaces: [
    { ...project().workspaces[0], terminals: 6 },
    { ...project().workspaces[0], id: "p1-w2", terminals: 3 },
  ] };
  useIdeProjectsStore.setState({ projects: [withTwo] });
  render(<IdeProjectTree />);
  // Open, the rows carry their own counts; folded, the header sums them.
  expect(screen.queryByLabelText("9 agent sessions")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Collapse App" }));
  expect(screen.getByLabelText("9 agent sessions").textContent).toBe("9");
});

it("renders a project whose only workspace shares its name as one row", async () => {
  const solo = { ...project(), workspaces: [{ ...project().workspaces[0], name: "app", status: "closed", live_terminals: 0, terminals: 4 }] } as IdeProject;
  useIdeProjectsStore.setState({ projects: [solo], activeWorkspaceId: null });
  render(<IdeProjectTree />);
  expect(screen.queryByRole("button", { name: /Collapse App|Expand App/ })).toBeNull();
  expect(screen.getAllByText(/^app$/i)).toHaveLength(1);
  expect(screen.getByLabelText("4 agent sessions")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Open App" }));
  expect(useIdeProjectsStore.getState().action).toMatchObject({ kind: "activate-workspace", workspaceId: "p1-w1" });
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  const menu = screen.getByRole("menu", { name: "Workspace actions for app" });
  expect(menu.textContent).toContain("Rename project");
  expect(menu.textContent).toContain("Reopen workspace");
  expect(menu.textContent).not.toContain("Rename workspace");
  expect(menu.textContent).not.toContain("Remove workspace");
});

it("marks a queued workspace before the active workspace changes", () => {
  render(<IdeProjectTree />);
  act(() => useIdeProjectsStore.getState().setPendingWorkspaceId("p1-w1"));
  const row = screen.getByTestId("ide-workspace-p1-w1");
  expect(row.getAttribute("aria-busy")).toBe("true");
  expect(row.textContent).toContain("Switching workspace");
});

it("gives every workspace row a ⋯ menu and dispatches Jarvis Live", () => {
  const base = project();
  useIdeProjectsStore.setState({ projects: [{ ...base, workspaces: [...base.workspaces, { ...base.workspaces[0], id: "p1-w2", name: "Other" }] }] });
  render(<IdeProjectTree />);
  // Not only the active row: a background workspace has actions too.
  fireEvent.click(screen.getByRole("button", { name: "Workspace actions for Other" }));
  expect(screen.getByRole("menu", { name: "Workspace actions for Other" })).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Workspace actions for Other" }));
  expect(screen.queryByRole("menu")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Jarvis Live" }));
  expect(useIdeProjectsStore.getState().action?.kind).toBe("toggle-voice");
});

it("pins and renames via the project API, then requests a guarded refresh", async () => {
  render(<IdeProjectTree />);
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Pin project" }));
  await waitFor(() => expect(patchProject).toHaveBeenCalledWith("p1", { pinned: true }));
  await waitFor(() => expect(useIdeProjectsStore.getState().refreshRequest?.nonce).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Rename project" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Rename App" }), { target: { value: "Better App" } });
  fireEvent.click(screen.getByRole("button", { name: "Save App" }));
  await waitFor(() => expect(patchProject).toHaveBeenCalledWith("p1", { name: "Better App" }));
  await waitFor(() => expect(useIdeProjectsStore.getState().refreshRequest?.nonce).toBe(2));
});

it("registers a legacy derived project only after PATCH 404, then retries once", async () => {
  patchProject.mockRejectedValueOnce(new ChatLibraryError("missing", 404)).mockResolvedValueOnce({});
  render(<IdeProjectTree />);
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Pin project" }));
  await waitFor(() => expect(patchProject).toHaveBeenCalledTimes(2));
  expect(openProject).toHaveBeenCalledTimes(1);
  expect(openProject).toHaveBeenCalledWith("/p1");
  expect(patchProject).toHaveBeenNthCalledWith(2, "p1", { pinned: true });
  expect(useIdeProjectsStore.getState().refreshRequest?.nonce).toBe(1);
});

it("does not register or retry on non-404 metadata errors", async () => {
  patchProject.mockRejectedValueOnce(new ChatLibraryError("denied", 403));
  render(<IdeProjectTree />);
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Pin project" }));
  await waitFor(() => expect(patchProject).toHaveBeenCalledTimes(1));
  expect(openProject).not.toHaveBeenCalled();
  expect(useIdeProjectsStore.getState().refreshRequest).toBeNull();
});

it("submits only one metadata mutation when a project action is pressed twice", async () => {
  let finish!: (value: unknown) => void;
  patchProject.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  render(<IdeProjectTree />);
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Pin project" }));
  // The menu closes on the first press; reopening and pressing again while the
  // first request is still in flight must not send a second one.
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  act(() => { fireEvent.click(screen.getByRole("menuitem", { name: "Pin project" })); });
  expect(patchProject).toHaveBeenCalledTimes(1);
  act(() => finish({}));
  await waitFor(() => expect(useIdeProjectsStore.getState().refreshRequest?.nonce).toBe(1));
});

it("opens a workspace menu on right-click with rename and remove", () => {
  render(<IdeProjectTree />);
  const row = screen.getByTestId("ide-workspace-row-p1-w1");
  fireEvent.contextMenu(row);
  expect(screen.getByTestId("ide-workspace-menu")).toBeDefined();
  expect(screen.getByTestId("ide-workspace-menu-rename").textContent).toContain("Rename workspace");
  expect(screen.getByTestId("ide-workspace-menu-close").textContent).toContain("Remove workspace");
  expect(screen.getByTestId("ide-workspace-menu-add").textContent).toContain("Add another agent");
});

it("renames a workspace from its right-click menu", async () => {
  render(<IdeProjectTree />);
  fireEvent.contextMenu(screen.getByTestId("ide-workspace-row-p1-w1"));
  fireEvent.click(screen.getByTestId("ide-workspace-menu-rename"));
  fireEvent.change(screen.getByRole("textbox", { name: "Rename Work" }), { target: { value: "Better Work" } });
  fireEvent.click(screen.getByRole("button", { name: "Save Work" }));
  await waitFor(() => expect(renameWorkspace).toHaveBeenCalledWith("p1-w1", "Better Work"));
  await waitFor(() => expect(useIdeProjectsStore.getState().refreshRequest?.nonce).toBe(1));
});

it("asks for confirmation before removing a workspace", async () => {
  render(<IdeProjectTree />);
  fireEvent.contextMenu(screen.getByTestId("ide-workspace-row-p1-w1"));
  fireEvent.click(screen.getByTestId("ide-workspace-menu-close"));
  expect(screen.getByTestId("ide-workspace-confirm-close")).toBeDefined();
  expect(removeWorkspace).not.toHaveBeenCalled();
  fireEvent.click(screen.getByTestId("ide-workspace-confirm-close-confirm"));
  // Remove, not close: a close would leave a closed row behind in the sidebar.
  await waitFor(() => expect(removeWorkspace).toHaveBeenCalledWith("p1-w1"));
  expect(closeWorkspace).not.toHaveBeenCalled();
  await waitFor(() => expect(useIdeProjectsStore.getState().refreshRequest?.nonce).toBe(1));
});

it("removes a closed workspace too", async () => {
  useIdeProjectsStore.setState({
    projects: [{
      ...project(),
      workspaces: [{ ...project().workspaces[0], status: "closed", live_terminals: 0 } as IdeProject["workspaces"][number]],
    }],
    activeWorkspaceId: null,
  });
  render(<IdeProjectTree />);
  fireEvent.contextMenu(screen.getByTestId("ide-workspace-row-p1-w1"));
  expect(screen.getByTestId("ide-workspace-menu-close").textContent).toContain("Remove workspace");
  fireEvent.click(screen.getByTestId("ide-workspace-menu-close"));
  fireEvent.click(screen.getByTestId("ide-workspace-confirm-close-confirm"));
  await waitFor(() => expect(removeWorkspace).toHaveBeenCalledWith("p1-w1"));
});

it("asks for confirmation before deleting a project with no open workspaces", async () => {
  useIdeProjectsStore.setState({
    projects: [{
      ...project(),
      workspaces: [{ ...project().workspaces[0], status: "closed", live_terminals: 0 } as IdeProject["workspaces"][number]],
    }],
    activeWorkspaceId: null,
  });
  render(<IdeProjectTree />);
  fireEvent.contextMenu(screen.getByTestId("ide-project-header-p1"));
  expect(screen.getByTestId("ide-project-menu")).toBeDefined();
  fireEvent.click(screen.getByTestId("ide-project-menu-delete"));
  expect(screen.getByTestId("ide-project-confirm-delete")).toBeDefined();
  expect(deleteProject).not.toHaveBeenCalled();
  fireEvent.click(screen.getByTestId("ide-project-confirm-delete-confirm"));
  // The closed workspace is forgotten too, or it would re-derive the project row.
  await waitFor(() => expect(removeWorkspace).toHaveBeenCalledWith("p1-w1"));
  await waitFor(() => expect(deleteProject).toHaveBeenCalledWith("p1"));
});

it("says to restart when the backend predates workspace removal", async () => {
  const { useEventStore } = await import("@/store/events");
  const pushToast = vi.fn();
  useEventStore.setState({ pushToast } as never);
  removeWorkspace.mockRejectedValue(new IdeApiError("Not Found", 404));
  render(<IdeProjectTree />);
  fireEvent.contextMenu(screen.getByTestId("ide-workspace-row-p1-w1"));
  fireEvent.click(screen.getByTestId("ide-workspace-menu-close"));
  fireEvent.click(screen.getByTestId("ide-workspace-confirm-close-confirm"));
  await waitFor(() => expect(pushToast).toHaveBeenCalledWith("error", expect.stringContaining("restart the app")));
});

it("removes open workspaces when a project is deleted", async () => {
  render(<IdeProjectTree />);
  fireEvent.contextMenu(screen.getByTestId("ide-project-header-p1"));
  fireEvent.click(screen.getByTestId("ide-project-menu-delete"));
  const confirm = screen.getByTestId("ide-project-confirm-delete-confirm");
  expect((confirm as HTMLButtonElement).disabled).toBe(false);
  expect(deleteProject).not.toHaveBeenCalled();
  fireEvent.click(confirm);
  await waitFor(() => expect(removeWorkspace).toHaveBeenCalledWith("p1-w1"));
  await waitFor(() => expect(deleteProject).toHaveBeenCalledWith("p1"));
  await waitFor(() => expect(useIdeProjectsStore.getState().refreshRequest?.nonce).toBe(1));
});

it("offers the project's real launchers and runs them", async () => {
  render(<IdeProjectTree />);
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  await screen.findByRole("menuitem", { name: "Open on GitHub" });
  const menu = screen.getByRole("menu", { name: "Project actions for App" });
  expect(Array.from(menu.querySelectorAll('[role="menuitem"]')).map((item) => item.textContent)).toEqual([
    "New workspace", "New workspace in a worktreeOwn folder and branch for its agents", "Open in VS Code", expect.stringMatching(/^Show in /), "Open on GitHub",
    "Rename project", "Pin project", "Delete project",
  ]);
  fireEvent.click(screen.getByRole("menuitem", { name: "Open in VS Code" }));
  await waitFor(() => expect(openProjectIn).toHaveBeenCalledWith("p1", "code"));
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Open on GitHub" }));
  await waitFor(() => expect(openProjectIn).toHaveBeenCalledWith("p1", "remote"));
});

it("hides launchers the backend cannot offer", async () => {
  fetchProjectLaunchers.mockRejectedValue(new Error("Not Found"));
  render(<IdeProjectTree />);
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  await waitFor(() => expect(fetchProjectLaunchers).toHaveBeenCalled());
  expect(screen.queryByRole("menuitem", { name: /Open in|Open on|Show in/ })).toBeNull();
  expect(screen.getByRole("menuitem", { name: "New workspace" })).toBeDefined();
});

it("adds an agent, duplicates the line-up and interrupts only working agents", async () => {
  render(<IdeProjectTree />);
  fireEvent.click(screen.getByRole("button", { name: "Workspace actions for Work" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: /Interrupt 1 working agent/ }));
  await waitFor(() => expect(interruptTerminal).toHaveBeenCalledWith("T1", "p1-w1"));
  expect(interruptTerminal).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByRole("button", { name: "Workspace actions for Work" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: /Duplicate workspace/ }));
  await waitFor(() => expect(startIdeSession).toHaveBeenCalledWith(
    "/p1", [{ agent: "claude" }, { agent: "codex", account: "work" }], { projectId: "p1", name: "Work copy" },
  ));

  fireEvent.click(screen.getByRole("button", { name: "Workspace actions for Work" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Add another agent" }));
  await waitFor(() => expect(addTerminal).toHaveBeenCalledWith({ workspace_id: "p1-w1" }));
});

it("opens the Git panel and a worktree workspace from the menus", async () => {
  render(<IdeProjectTree />);
  fireEvent.click(screen.getByRole("button", { name: "Workspace actions for Work" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: /^Git/ }));
  expect(useIdeProjectsStore.getState().action).toMatchObject({ kind: "git-panel", workspaceId: "p1-w1" });
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: /New workspace in a worktree/ }));
  expect(useIdeProjectsStore.getState().action).toMatchObject({ kind: "new-workspace", projectId: "p1", worktree: true });
});

it("offers a closed workspace only what works on it", async () => {
  useIdeProjectsStore.setState({
    projects: [{
      ...project(),
      workspaces: [{ ...project().workspaces[0], status: "closed", live_terminals: 0 } as IdeProject["workspaces"][number]],
    }],
    activeWorkspaceId: null,
  });
  render(<IdeProjectTree />);
  fireEvent.contextMenu(screen.getByTestId("ide-workspace-row-p1-w1"));
  await screen.findByRole("menuitem", { name: "Open in VS Code" });
  expect(screen.getByRole("menuitem", { name: "Reopen workspace" })).toBeDefined();
  for (const missing of [/Rename workspace/, /Add another agent/, /Duplicate/, /Interrupt/, /^Git/]) {
    expect(screen.queryByRole("menuitem", { name: missing })).toBeNull();
  }
});
