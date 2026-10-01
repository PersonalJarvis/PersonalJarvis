/**
 * Sidebar rows a terminal pane can be dropped on.
 *
 * The grid finds a drop target by these attributes under the pointer, so they
 * are the whole contract: only an OPEN workspace can take a running pane (a
 * closed one has no grid to re-join it), and a one-row project carries them on
 * its single header, because that header is the workspace row there.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { IdeProjectTree } from "./IdeProjectTree";
import { useIdeProjectsStore } from "@/store/ideProjects";
import type { IdeProject } from "@/lib/agenticIdeApi";

vi.mock("@/lib/agenticIdeApi", () => ({ IdeApiError: class extends Error {} }));
vi.mock("@/lib/chatLibraryApi", () => ({ ChatLibraryError: class extends Error {} }));

function workspace(id: string, name: string, status: "open" | "closed" = "open") {
  return { id, name, status, live_terminals: 1, terminals: 1, restorable: true };
}

function project(id: string, name: string, workspaces: ReturnType<typeof workspace>[]): IdeProject {
  return {
    id, path: `/${id}`, name, color: null, pinned: false, archived: false, scratch: false,
    created_at: 0, last_opened_at: 0, exists: true, chats: 0, workspaces,
  } as unknown as IdeProject;
}

beforeEach(() => {
  localStorage.clear();
  useIdeProjectsStore.setState({
    projects: [
      project("p1", "Personal Jarvis", [workspace("w1", "Personal Jarvis 2"), workspace("w2", "Blog"), workspace("w3", "Old", "closed")]),
      project("p2", "Website", [workspace("w4", "Website")]),
    ],
    activeWorkspaceId: "w1",
    pendingWorkspaceId: null,
    refreshRequest: null,
    action: null,
  });
});
afterEach(cleanup);

it("lets a pane be dropped on open workspaces only", () => {
  render(<IdeProjectTree />);

  const blog = screen.getByTestId("ide-workspace-row-w2");
  expect(blog.getAttribute("data-pane-drop-workspace")).toBe("w2");
  expect(blog.getAttribute("data-pane-drop-name")).toBe("Blog");
  expect(screen.getByTestId("ide-workspace-row-w3").hasAttribute("data-pane-drop-workspace")).toBe(false);
});

it("puts the drop target on a one-row project's header", () => {
  render(<IdeProjectTree />);

  expect(screen.getByTestId("ide-project-header-p2").getAttribute("data-pane-drop-workspace")).toBe("w4");
  expect(screen.getByTestId("ide-project-header-p1").hasAttribute("data-pane-drop-workspace")).toBe(false);
});
