import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
      "agentic_grid.explorer.open_file": "Open in app",
      "agentic_grid.explorer.file": "File",
      "agentic_grid.explorer.folder": "Folder",
      "agentic_grid.explorer.symlink": "Symbolic link",
      "agentic_grid.explorer.open_hint":
        "Click a file to open it here in the workspace.",
      "agentic_grid.explorer.truncated":
        "This folder has more entries than can be shown at once.",
    })[key] ?? key,
}));

vi.mock("@/lib/agenticIdeApi", () => ({
  fetchWorkspaceFiles: vi.fn(),
}));

import * as api from "@/lib/agenticIdeApi";
import { extractPaneDrop } from "./paneDrop";
import { WorkspaceExplorer } from "./WorkspaceExplorer";

/** Minimal DataTransfer stand-in — jsdom has no real one. */
function fakeDataTransfer(): DataTransfer {
  const store = new Map<string, string>();
  return {
    setData: (type: string, value: string) => store.set(type, value),
    getData: (type: string) => store.get(type) ?? "",
    files: [],
  } as unknown as DataTransfer;
}

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
          {
            name: "README.md",
            path: "README.md",
            is_directory: false,
            is_symlink: false,
            size: 20,
          },
          {
            name: "LICENSE",
            path: "LICENSE",
            is_directory: false,
            is_symlink: false,
            size: 12,
          },
        ],
      };
    });
  });

  it("lazy-loads the complete tree and previews files from their relative paths", async () => {
    const onOpenFile = vi.fn();
    render(
      <WorkspaceExplorer
        workspaceId="workspace-1"
        rootName="fallback"
        onClose={vi.fn()}
        onOpenFile={onOpenFile}
      />,
    );

    expect(await screen.findByText(".gitignore")).toBeTruthy();
    const explorer = screen.getByTestId("workspace-explorer");
    expect(explorer.className).toContain("w-full");
    expect(explorer.style.background).toContain("0.22");
    expect(screen.getByText("project")).toBeTruthy();
    expect(
      screen
        .getByRole("treeitem", { name: /README\.md, Markdown README/i })
        .querySelector('[data-material-icon="readme"]'),
    ).toBeTruthy();
    expect(
      screen.getByRole("treeitem", { name: /LICENSE, File/i }),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("treeitem", { name: /src/i }));
    expect(await screen.findByText("main.ts")).toBeTruthy();
    expect(api.fetchWorkspaceFiles).toHaveBeenCalledWith("workspace-1", "src");

    fireEvent.click(screen.getByRole("treeitem", { name: /main\.ts/i }));
    expect(onOpenFile).toHaveBeenCalledWith("src/main.ts", expect.any(HTMLElement));

    fireEvent.click(screen.getByRole("button", { name: "Open in app main.ts" }));
    expect(onOpenFile).toHaveBeenCalledTimes(2);
  });

  it("lets the close button collapse the panel", async () => {
    const onClose = vi.fn();
    render(
      <WorkspaceExplorer
        workspaceId="workspace-1"
        rootName="project"
        onClose={onClose}
        onOpenFile={vi.fn()}
      />,
    );

    await screen.findByRole("treeitem", { name: /\.gitignore/i });

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
        onOpenFile={vi.fn()}
      />,
    );

    expect((await screen.findByRole("alert")).textContent).toBe(
      "The folder could not be loaded.",
    );
  });

  it("hands a dragged row to a pane as an absolute path", async () => {
    render(
      <WorkspaceExplorer
        workspaceId="workspace-1"
        rootName="project"
        rootPath="/home/me/project"
        onClose={vi.fn()}
        onOpenFile={vi.fn()}
      />,
    );

    await screen.findByText("README.md");
    const row = screen.getByTestId("explorer-entry-README.md");
    expect(row.getAttribute("draggable")).toBe("true");

    const dataTransfer = fakeDataTransfer();
    fireEvent.dragStart(row, { dataTransfer });
    // What a terminal pane will actually read out of the drop.
    expect(extractPaneDrop(dataTransfer).paths).toEqual([
      "/home/me/project/README.md",
    ]);
  });

  it("lets a FOLDER be dragged too, not only a file", async () => {
    render(
      <WorkspaceExplorer
        workspaceId="workspace-1"
        rootName="project"
        rootPath="/home/me/project"
        onClose={vi.fn()}
        onOpenFile={vi.fn()}
      />,
    );

    await screen.findByText("src");
    const dataTransfer = fakeDataTransfer();
    fireEvent.dragStart(screen.getByTestId("explorer-entry-src"), { dataTransfer });
    expect(extractPaneDrop(dataTransfer).paths).toEqual(["/home/me/project/src"]);
  });

  it("does not offer a drag before the workspace folder is known", async () => {
    // A row that lifts but carries nothing would swallow the click that opens
    // the file, so it must not lift at all.
    render(
      <WorkspaceExplorer
        workspaceId="workspace-1"
        rootName="project"
        onClose={vi.fn()}
        onOpenFile={vi.fn()}
      />,
    );

    await screen.findByText("README.md");
    expect(
      screen.getByTestId("explorer-entry-README.md").getAttribute("draggable"),
    ).toBe("false");
  });
});
