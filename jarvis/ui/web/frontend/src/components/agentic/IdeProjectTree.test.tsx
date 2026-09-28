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
const robustCopy = vi.hoisted(() => vi.fn());
vi.mock("@/lib/clipboard", () => ({ robustCopy }));
vi.mock("@/lib/chatLibraryApi", () => ({ patchProject, openProject, deleteProject, reorderProjects, revealProject,
  ChatLibraryError: class extends Error { constructor(message: string, readonly status: number) { super(message); } },
}));

const renameWorkspace = vi.hoisted(() => vi.fn());
const closeWorkspace = vi.hoisted(() => vi.fn());
const removeWorkspace = vi.hoisted(() => vi.fn());
const reorderWorkspaces = vi.hoisted(() => vi.fn());
vi.mock("@/lib/agenticIdeApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/agenticIdeApi")>();
  return { ...actual, renameWorkspace, closeWorkspace, removeWorkspace, reorderWorkspaces };
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
  expect(selected.className).toContain("min-h-10");
  expect(screen.getByTestId("ide-project-p1").firstElementChild?.className).toContain("min-h-11");
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
  expect(screen.getByLabelText("9 agent sessions").textContent).toBe("9");
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
  fireEvent.click(screen.getByRole("button", { name: "Workspace actions for Work" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Workspace options…" }));
  expect(useIdeProjectsStore.getState().action).toMatchObject({ kind: "workspace-options", workspaceId: "p1-w1" });
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
  expect(screen.getByTestId("ide-workspace-menu-open")).toBeDefined();
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

it("groups project actions and runs folder actions", async () => {
  render(<IdeProjectTree />);
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  const menu = screen.getByRole("menu", { name: "Project actions for App" });
  expect(menu.querySelectorAll('[role="group"]').length).toBe(3);
  expect(Array.from(menu.querySelectorAll('[role="menuitem"]')).map((item) => item.textContent)).toEqual([
    "New workspace", "Open folder", "Copy folder path", "Rename project", "Pin project", "Delete project",
  ]);
  fireEvent.click(screen.getByRole("menuitem", { name: "Open folder" }));
  await waitFor(() => expect(revealProject).toHaveBeenCalledWith("p1"));
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Copy folder path" }));
  await waitFor(() => expect(robustCopy).toHaveBeenCalledWith("/p1"));
});

it("closes an open workspace but keeps it, separately from removing it", async () => {
  render(<IdeProjectTree />);
  fireEvent.click(screen.getByRole("button", { name: "Workspace actions for Work" }));
  fireEvent.click(screen.getByTestId("ide-workspace-menu-stop"));
  expect(screen.getByRole("dialog", { name: "Close Work?" })).toBeDefined();
  fireEvent.click(screen.getByTestId("ide-workspace-confirm-close-confirm"));
  await waitFor(() => expect(closeWorkspace).toHaveBeenCalledWith("p1-w1"));
  expect(removeWorkspace).not.toHaveBeenCalled();
});

it("offers no close or rename on a closed workspace", () => {
  useIdeProjectsStore.setState({
    projects: [{
      ...project(),
      workspaces: [{ ...project().workspaces[0], status: "closed", live_terminals: 0 } as IdeProject["workspaces"][number]],
    }],
    activeWorkspaceId: null,
  });
  render(<IdeProjectTree />);
  fireEvent.contextMenu(screen.getByTestId("ide-workspace-row-p1-w1"));
  expect(screen.queryByTestId("ide-workspace-menu-stop")).toBeNull();
  expect((screen.getByRole("menuitem", { name: /Rename workspace/ }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole("menuitem", { name: "Reopen workspace" })).toBeDefined();
});
