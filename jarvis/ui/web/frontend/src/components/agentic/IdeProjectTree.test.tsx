import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { IdeProjectTree } from "./IdeProjectTree";
import { useIdeProjectsStore } from "@/store/ideProjects";
import type { IdeProject } from "@/lib/agenticIdeApi";
import { ChatLibraryError } from "@/lib/chatLibraryApi";

const patchProject = vi.hoisted(() => vi.fn());
const openProject = vi.hoisted(() => vi.fn());
vi.mock("@/lib/chatLibraryApi", () => ({ patchProject, openProject,
  ChatLibraryError: class extends Error { constructor(message: string, readonly status: number) { super(message); } },
}));

const project = (id = "p1", pinned = false) => ({ id, path: `/${id}`, name: id === "p1" ? "App" : "New App",
  color: null, pinned, archived: false, scratch: false, created_at: 0, last_opened_at: 0, exists: true, chats: 0,
  workspaces: [{ id: `${id}-w1`, name: "Work", status: "open", live_terminals: 1, terminals: 1, restorable: true }] }) as IdeProject;

beforeEach(() => {
  localStorage.clear();
  patchProject.mockReset().mockResolvedValue({});
  openProject.mockReset().mockResolvedValue({ id: "p1" });
  useIdeProjectsStore.setState({ projects: [project()], activeWorkspaceId: "p1-w1", pendingWorkspaceId: null, refreshRequest: null, action: null });
});
afterEach(cleanup);

it("keeps a manual collapse across polling and remount", () => {
  const { unmount } = render(<IdeProjectTree />);
  const selected = screen.getByTestId("ide-workspace-p1-w1");
  expect(selected.className).toContain("bg-muted");
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

it("pins and renames via the project API, then requests a guarded refresh", async () => {
  render(<IdeProjectTree />);
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  fireEvent.click(screen.getByRole("button", { name: "Pin project" }));
  await waitFor(() => expect(patchProject).toHaveBeenCalledWith("p1", { pinned: true }));
  await waitFor(() => expect(useIdeProjectsStore.getState().refreshRequest?.nonce).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  fireEvent.click(screen.getByRole("button", { name: "Rename project" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Rename App" }), { target: { value: "Better App" } });
  fireEvent.click(screen.getByRole("button", { name: "Save App" }));
  await waitFor(() => expect(patchProject).toHaveBeenCalledWith("p1", { name: "Better App" }));
  await waitFor(() => expect(useIdeProjectsStore.getState().refreshRequest?.nonce).toBe(2));
});

it("registers a legacy derived project only after PATCH 404, then retries once", async () => {
  patchProject.mockRejectedValueOnce(new ChatLibraryError("missing", 404)).mockResolvedValueOnce({});
  render(<IdeProjectTree />);
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  fireEvent.click(screen.getByRole("button", { name: "Pin project" }));
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
  fireEvent.click(screen.getByRole("button", { name: "Pin project" }));
  await waitFor(() => expect(patchProject).toHaveBeenCalledTimes(1));
  expect(openProject).not.toHaveBeenCalled();
  expect(useIdeProjectsStore.getState().refreshRequest).toBeNull();
});

it("submits only one metadata mutation when a project action is pressed twice", async () => {
  let finish!: (value: unknown) => void;
  patchProject.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  render(<IdeProjectTree />);
  fireEvent.click(screen.getByRole("button", { name: "Project actions for App" }));
  const pin = screen.getByRole("button", { name: "Pin project" });
  act(() => { fireEvent.click(pin); fireEvent.click(pin); });
  expect(patchProject).toHaveBeenCalledTimes(1);
  act(() => finish({}));
  await waitFor(() => expect(useIdeProjectsStore.getState().refreshRequest?.nonce).toBe(1));
});
