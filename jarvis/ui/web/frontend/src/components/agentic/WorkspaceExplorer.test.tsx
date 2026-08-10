import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushToast = vi.fn();

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (state: { pushToast: typeof pushToast }) => unknown) =>
    selector({ pushToast }),
}));

vi.mock("@/i18n", () => ({
  useT: () => (key: string) =>
    ({
      "agentic_grid.explorer.title": "Explorer",
      "agentic_grid.explorer.toggle": "Show or hide the workspace explorer",
      "agentic_grid.explorer.refresh": "Refresh the file tree",
      "agentic_grid.explorer.close": "Close the explorer",
      "agentic_grid.explorer.loading": "Loading…",
      "agentic_grid.explorer.empty": "Empty folder",
      "agentic_grid.explorer.load_failed": "The folder could not be loaded.",
      "agentic_grid.explorer.open_failed": "The file could not be opened.",
      "agentic_grid.explorer.open_hint":
        "Double-click a file to open it in your system editor.",
      "agentic_grid.explorer.truncated":
        "This folder has more entries than can be shown at once.",
    })[key] ?? key,
}));

vi.mock("@/lib/agenticIdeApi", () => ({
  fetchWorkspaceFiles: vi.fn(),
  openTerminalTarget: vi.fn(),
}));

import * as api from "@/lib/agenticIdeApi";
import { WorkspaceExplorer } from "./WorkspaceExplorer";

describe("WorkspaceExplorer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.fetchWorkspaceFiles).mockImplementation(async (_workspace, path = "") => {
      if (path === "src") {
        return {
          workspace_id: "workspace-1",
          root_name: "project",
          path: "src",
          truncated: false,
          entries: [
            {
              name: "main.ts",
              path: "src/main.ts",
              is_directory: false,
              is_symlink: false,
              size: 12,
            },
          ],
        };
      }
      return {
        workspace_id: "workspace-1",
        root_name: "project",
        path: "",
        truncated: false,
        entries: [
          {
            name: "src",
            path: "src",
            is_directory: true,
            is_symlink: false,
          },
          {
            name: ".gitignore",
            path: ".gitignore",
            is_directory: false,
            is_symlink: false,
            size: 8,
          },
        ],
      };
    });
    vi.mocked(api.openTerminalTarget).mockResolvedValue({
      opened: true,
      kind: "file",
      path: "src/main.ts",
    });
  });

  it("lazy-loads the complete tree and opens files from their relative paths", async () => {
    render(
      <WorkspaceExplorer
        workspaceId="workspace-1"
        rootName="fallback"
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByText(".gitignore")).toBeTruthy();
    expect(screen.getByTestId("workspace-explorer").className).toContain(
      "jarvis-panel-surface",
    );
    expect(screen.getByText("project")).toBeTruthy();

    fireEvent.click(screen.getByRole("treeitem", { name: /src/i }));
    expect(await screen.findByText("main.ts")).toBeTruthy();
    expect(api.fetchWorkspaceFiles).toHaveBeenCalledWith("workspace-1", "src");

    fireEvent.doubleClick(screen.getByRole("treeitem", { name: /main\.ts/i }));
    await waitFor(() =>
      expect(api.openTerminalTarget).toHaveBeenCalledWith(
        "workspace-1",
        "src/main.ts",
      ),
    );
  });

  it("keeps open failures visible and lets the close button collapse the panel", async () => {
    const onClose = vi.fn();
    vi.mocked(api.openTerminalTarget).mockRejectedValue(new Error("No editor is available."));
    render(
      <WorkspaceExplorer
        workspaceId="workspace-1"
        rootName="project"
        onClose={onClose}
      />,
    );

    const file = await screen.findByRole("treeitem", { name: /\.gitignore/i });
    fireEvent.doubleClick(file);
    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith("error", "No editor is available."),
    );

    fireEvent.click(screen.getByRole("button", { name: "Close the explorer" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("shows a localized message when a directory cannot be loaded", async () => {
    vi.mocked(api.fetchWorkspaceFiles).mockRejectedValue(new Error("Not Found"));

    render(
      <WorkspaceExplorer
        workspaceId="workspace-1"
        rootName="project"
        onClose={vi.fn()}
      />,
    );

    expect((await screen.findByRole("alert")).textContent).toBe(
      "The folder could not be loaded.",
    );
  });
});
