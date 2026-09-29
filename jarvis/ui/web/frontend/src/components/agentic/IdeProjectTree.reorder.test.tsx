import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { IdeProjectTree } from "./IdeProjectTree";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { IdeApiError, type IdeProject } from "@/lib/agenticIdeApi";

const reorderWorkspaces = vi.hoisted(() => vi.fn());
vi.mock("@/lib/agenticIdeApi", () => {
  class MockIdeApiError extends Error {
    constructor(
      message: string,
      readonly status: number,
    ) {
      super(message);
      this.name = "IdeApiError";
    }
  }
  return { reorderWorkspaces, IdeApiError: MockIdeApiError };
});
vi.mock("@/lib/chatLibraryApi", () => ({
  patchProject: vi.fn(async () => ({})),
  openProject: vi.fn(async () => ({ id: "p1" })),
  ChatLibraryError: class extends Error {
    constructor(message: string, readonly status: number) {
      super(message);
    }
  },
}));

function workspace(id: string, name: string) {
  return { id, name, status: "open", live_terminals: 1, terminals: 1, restorable: true };
}

function projectWithThree(): IdeProject {
  return {
    id: "p1",
    path: "/p1",
    name: "App",
    color: null,
    pinned: false,
    archived: false,
    scratch: false,
    created_at: 0,
    last_opened_at: 0,
    exists: true,
    chats: 0,
    workspaces: [workspace("w1", "First"), workspace("w2", "Second"), workspace("w3", "Third")],
  } as IdeProject;
}

beforeEach(() => {
  localStorage.clear();
  reorderWorkspaces.mockReset().mockResolvedValue({});
  useIdeProjectsStore.setState({
    projects: [projectWithThree()],
    activeWorkspaceId: "w1",
    pendingWorkspaceId: null,
    refreshRequest: null,
    action: null,
  });
});
afterEach(cleanup);

it("marks open workspaces draggable with a reorder hint", () => {
  render(<IdeProjectTree />);
  const row = screen.getByTestId("ide-workspace-row-w1");
  expect(row.getAttribute("draggable")).toBe("true");
  expect(screen.getByTestId("ide-workspace-w1").getAttribute("title")).toContain("drag to reorder");
});

it("moves a workspace with Alt plus arrow keys", async () => {
  render(<IdeProjectTree />);
  fireEvent.keyDown(screen.getByTestId("ide-workspace-w1"), { key: "ArrowDown", altKey: true });
  await waitFor(() => expect(reorderWorkspaces).toHaveBeenCalledWith(["w2", "w1", "w3"]));
  await waitFor(() => expect(useIdeProjectsStore.getState().refreshRequest?.nonce).toBe(1));
});

it("ignores Alt plus arrow keys at the list edges", () => {
  render(<IdeProjectTree />);
  fireEvent.keyDown(screen.getByTestId("ide-workspace-w1"), { key: "ArrowUp", altKey: true });
  fireEvent.keyDown(screen.getByTestId("ide-workspace-w3"), { key: "ArrowDown", altKey: true });
  expect(reorderWorkspaces).not.toHaveBeenCalled();
});

it("drags a workspace below its neighbour", async () => {
  render(<IdeProjectTree />);
  const row = screen.getByTestId("ide-workspace-row-w1");
  const target = screen.getByTestId("ide-workspace-row-w2");
  const data = new Map<string, string>();
  const dataTransfer = {
    types: ["application/x-jarvis-workspace-id"],
    effectAllowed: "",
    dropEffect: "",
    setData: (kind: string, value: string) => void data.set(kind, value),
    getData: (kind: string) => data.get(kind) ?? "",
  };
  fireEvent.dragStart(row, { dataTransfer });
  target.getBoundingClientRect = () => ({ top: 0, height: 40, left: 0, width: 200 } as DOMRect);
  fireEvent.dragOver(target, { dataTransfer, clientY: 35 });
  fireEvent.drop(target, { dataTransfer, clientY: 35 });
  await waitFor(() => expect(reorderWorkspaces).toHaveBeenCalledWith(["w2", "w1", "w3"]));
});

it("shows a toast instead of reordering when the backend rejects", async () => {
  reorderWorkspaces.mockRejectedValueOnce(new Error("stale list"));
  const { useEventStore } = await import("@/store/events");
  const pushToast = vi.fn();
  useEventStore.setState({ pushToast } as never);
  render(<IdeProjectTree />);
  act(() => {
    fireEvent.keyDown(screen.getByTestId("ide-workspace-w1"), { key: "ArrowDown", altKey: true });
  });
  await waitFor(() => expect(pushToast).toHaveBeenCalledWith("error", "stale list"));
});

it("names the restart when the backend predates the reorder endpoint", async () => {
  reorderWorkspaces.mockRejectedValueOnce(new IdeApiError("Method Not Allowed", 405));
  const { useEventStore } = await import("@/store/events");
  const pushToast = vi.fn();
  useEventStore.setState({ pushToast } as never);
  render(<IdeProjectTree />);
  act(() => {
    fireEvent.keyDown(screen.getByTestId("ide-workspace-w1"), { key: "ArrowDown", altKey: true });
  });
  await waitFor(() =>
    expect(pushToast).toHaveBeenCalledWith(
      "error",
      "This view is newer than the backend — restart the app and try again.",
    ),
  );
});
