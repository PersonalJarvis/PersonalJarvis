import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { IdeProjectTree } from "./IdeProjectTree";
import { useIdeProjectsStore } from "@/store/ideProjects";
import type { IdeProject } from "@/lib/agenticIdeApi";

import { ChatLibraryError } from "@/lib/chatLibraryApi";

const reorderProjects = vi.hoisted(() => vi.fn());
const reorderWorkspaces = vi.hoisted(() => vi.fn());
vi.mock("@/lib/chatLibraryApi", () => {
  class MockChatLibraryError extends Error {
    constructor(
      message: string,
      readonly status: number,
    ) {
      super(message);
    }
  }
  return {
    patchProject: vi.fn(async () => ({})),
    openProject: vi.fn(async () => ({ id: "p1" })),
    reorderProjects,
    ChatLibraryError: MockChatLibraryError,
  };
});
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

function folder(id: string, name: string, pinned = false): IdeProject {
  return {
    id,
    path: `/${id}`,
    name,
    color: null,
    pinned,
    archived: false,
    scratch: false,
    created_at: 0,
    last_opened_at: 0,
    exists: true,
    chats: 0,
    workspaces: [],
  } as IdeProject;
}

beforeEach(() => {
  localStorage.clear();
  reorderProjects.mockReset().mockResolvedValue([]);
  reorderWorkspaces.mockReset().mockResolvedValue({});
  useIdeProjectsStore.setState({
    projects: [folder("p1", "AiGrokAgents"), folder("p2", "Personal Jarvis"), folder("p3", "workspace")],
    activeWorkspaceId: null,
    pendingWorkspaceId: null,
    refreshRequest: null,
    action: null,
  });
});
afterEach(cleanup);

it("marks folder headers draggable with a reorder hint", () => {
  render(<IdeProjectTree />);
  const header = screen.getByTestId("ide-project-header-p1");
  expect(header.getAttribute("draggable")).toBe("true");
  expect(screen.getByRole("button", { name: "Collapse AiGrokAgents" }).getAttribute("title")).toContain(
    "drag to reorder",
  );
});

it("moves a folder with Alt plus arrow keys", async () => {
  render(<IdeProjectTree />);
  fireEvent.keyDown(screen.getByRole("button", { name: "Collapse AiGrokAgents" }), {
    key: "ArrowDown",
    altKey: true,
  });
  await waitFor(() => expect(reorderProjects).toHaveBeenCalledWith(["p2", "p1", "p3"]));
  await waitFor(() => expect(useIdeProjectsStore.getState().refreshRequest?.nonce).toBe(1));
});

it("ignores Alt plus arrow keys at the folder list edges", () => {
  render(<IdeProjectTree />);
  fireEvent.keyDown(screen.getByRole("button", { name: "Collapse AiGrokAgents" }), {
    key: "ArrowUp",
    altKey: true,
  });
  fireEvent.keyDown(screen.getByRole("button", { name: "Expand workspace" }), {
    key: "ArrowDown",
    altKey: true,
  });
  expect(reorderProjects).not.toHaveBeenCalled();
});

it("drags a folder below its neighbour", async () => {
  render(<IdeProjectTree />);
  const source = screen.getByTestId("ide-project-header-p1");
  const target = screen.getByTestId("ide-project-header-p2");
  const data = new Map<string, string>();
  const dataTransfer = {
    types: ["application/x-jarvis-project-id"],
    effectAllowed: "",
    dropEffect: "",
    setData: (kind: string, value: string) => void data.set(kind, value),
    getData: (kind: string) => data.get(kind) ?? "",
  };
  fireEvent.dragStart(source, { dataTransfer });
  target.getBoundingClientRect = () => ({ top: 0, height: 44, left: 0, width: 300 } as DOMRect);
  fireEvent.dragOver(target, { dataTransfer, clientY: 40 });
  fireEvent.drop(target, { dataTransfer, clientY: 40 });
  await waitFor(() => expect(reorderProjects).toHaveBeenCalledWith(["p2", "p1", "p3"]));
});

it("keeps a pinned folder inside its own section", () => {
  useIdeProjectsStore.setState({
    projects: [folder("p1", "AiGrokAgents", true), folder("p2", "Personal Jarvis")],
  });
  render(<IdeProjectTree />);
  const source = screen.getByTestId("ide-project-header-p1");
  const target = screen.getByTestId("ide-project-header-p2");
  const data = new Map<string, string>();
  const dataTransfer = {
    types: ["application/x-jarvis-project-id"],
    effectAllowed: "",
    dropEffect: "",
    setData: (kind: string, value: string) => void data.set(kind, value),
    getData: (kind: string) => data.get(kind) ?? "",
  };
  fireEvent.dragStart(source, { dataTransfer });
  target.getBoundingClientRect = () => ({ top: 0, height: 44, left: 0, width: 300 } as DOMRect);
  fireEvent.dragOver(target, { dataTransfer, clientY: 40 });
  fireEvent.drop(target, { dataTransfer, clientY: 40 });
  expect(reorderProjects).not.toHaveBeenCalled();
});

it("shows a toast instead of reordering when the backend rejects", async () => {
  reorderProjects.mockRejectedValueOnce(new Error("stale list"));
  const { useEventStore } = await import("@/store/events");
  const pushToast = vi.fn();
  useEventStore.setState({ pushToast } as never);
  render(<IdeProjectTree />);
  act(() => {
    fireEvent.keyDown(screen.getByRole("button", { name: "Collapse AiGrokAgents" }), {
      key: "ArrowDown",
      altKey: true,
    });
  });
  await waitFor(() => expect(pushToast).toHaveBeenCalledWith("error", "stale list"));
});

it("names the restart when the backend predates the reorder endpoint", async () => {
  reorderProjects.mockRejectedValueOnce(new ChatLibraryError("Method Not Allowed", 405));
  const { useEventStore } = await import("@/store/events");
  const pushToast = vi.fn();
  useEventStore.setState({ pushToast } as never);
  render(<IdeProjectTree />);
  act(() => {
    fireEvent.keyDown(screen.getByRole("button", { name: "Collapse AiGrokAgents" }), {
      key: "ArrowDown",
      altKey: true,
    });
  });
  await waitFor(() =>
    expect(pushToast).toHaveBeenCalledWith(
      "error",
      "This view is newer than the backend — restart the app and try again.",
    ),
  );
});
