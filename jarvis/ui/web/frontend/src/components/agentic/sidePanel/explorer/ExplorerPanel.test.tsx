import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { ExplorerPanel } from "./ExplorerPanel";
import { WORKSPACE_PATH_TYPE } from "@/components/agentic/paneDrop";
import { activateTerminalLink } from "@/lib/terminalLinks";
import { useEventStore } from "@/store/events";
import { useIdeChatStore } from "@/store/ideChat";
import { useExplorerPathRouting, useIdeExplorerStore } from "@/store/ideExplorer";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";

const CHANGES = {
  workspace_id: "w1",
  available: true,
  branch: "main",
  truncated: false,
  reason: "",
  files: [
    {
      path: "src/app.ts",
      status: "modified",
      added: 3,
      removed: 1,
      is_directory: false,
      authors: [{ pane: "T2", history_id: "h2", agent: "codex", display_name: "Codex", last_edit_ms: 5 }],
    },
    { path: "old.md", status: "deleted", added: 0, removed: 4, is_directory: false },
  ],
};

const DIFF = {
  workspace_id: "w1",
  path: "src/app.ts",
  status: "modified",
  binary: false,
  added: 1,
  removed: 1,
  truncated: false,
  hunks: [
    {
      header: "@@ -1,2 +1,2 @@",
      lines: [
        { kind: "ctx", text: "keep", old_no: 1, new_no: 1 },
        { kind: "del", text: "before", old_no: 2, new_no: null },
        { kind: "add", text: "after", old_no: null, new_no: 2 },
      ],
    },
  ],
};

const calls: string[] = [];

function respond(url: string): unknown {
  if (url.includes("/changes")) return CHANGES;
  if (url.includes("/diff?")) return DIFF;
  if (url.includes("/files")) {
    return { workspace_id: "w1", root_name: "app", path: "", truncated: false, entries: [
      { name: "src", path: "src", is_directory: true, is_symlink: false },
      { name: "README.md", path: "README.md", is_directory: false, is_symlink: false },
    ] };
  }
  return {};
}

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    return new Response(JSON.stringify(respond(url)), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  useEventStore.setState({ activeSection: "agentic-ide" });
  useIdeChatStore.setState({ workspace: { id: "w1", name: "App", path: "/code/app" }, stagedPane: null });
  useIdeExplorerStore.setState({ opened: { changes: null, files: null } });
  useIdeSidePanelStore.setState({ open: false, tabs: ["agents", "changes", "files"], active: "agents" });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ExplorerPanel", () => {
  it("lists what changed — deleted red, edited green — with line counts and the branch", async () => {
    render(<ExplorerPanel view="changes" />);
    const rows = await screen.findAllByTestId("explorer-change-row");
    expect(rows.map((row) => row.dataset.path)).toEqual(["src/app.ts", "old.md"]);
    expect(rows[0].textContent).toContain("+3");
    expect(rows[0].textContent).toContain("−1");
    expect(rows[0].querySelector("[data-status]")?.className).toContain("text-success");
    expect(rows[1].querySelector("[data-status]")?.className).toContain("text-destructive");
    expect(screen.getByTestId("explorer-branch").textContent).toBe("main");
  });

  it("names the coding agent that changed a file, and only where one is known", async () => {
    render(<ExplorerPanel view="changes" />);
    const rows = await screen.findAllByTestId("explorer-change-row");
    const authors = rows[0].querySelector('[data-testid="explorer-change-authors"]');
    expect(authors?.textContent).toContain("Codex");
    expect(authors?.getAttribute("title")).toContain("T2");
    expect(rows[1].querySelector('[data-testid="explorer-change-authors"]')).toBeNull();
    expect(screen.queryByRole("tablist")).toBeNull();
  });

  it("hands a terminal the absolute path when a row is dragged", async () => {
    render(<ExplorerPanel view="changes" />);
    const [row] = await screen.findAllByTestId("explorer-change-row");
    const data: Record<string, string> = {};
    const dataTransfer = { setData: (type: string, value: string) => { data[type] = value; }, effectAllowed: "" };
    fireEvent.dragStart(row, { dataTransfer });
    expect(data[WORKSPACE_PATH_TYPE]).toBe("/code/app/src/app.ts");
  });

  it("opens a changed file as a diff with removed and added lines", async () => {
    render(<ExplorerPanel view="changes" />);
    const [row] = await screen.findAllByTestId("explorer-change-row");
    fireEvent.click(row);
    const diff = await screen.findByTestId("explorer-diff");
    expect(diff.querySelector('[data-kind="del"]')?.textContent).toContain("before");
    expect(diff.querySelector('[data-kind="add"]')?.textContent).toContain("after");
    expect(diff.querySelector('[data-kind="del"]')?.className).toContain("bg-destructive/10");
    expect(diff.querySelector('[data-kind="add"]')?.className).toContain("bg-success/10");
  });

  it("shows the folder as a lazy tree in the Folder tab", async () => {
    render(<ExplorerPanel view="files" />);
    await waitFor(() => expect(screen.getAllByTestId("explorer-tree-row")).toHaveLength(2));
    const rows = screen.getAllByTestId("explorer-tree-row");
    expect(rows.map((row) => row.dataset.path)).toEqual(["src", "README.md"]);
    // The folder holds a change, so it carries the marker while collapsed.
    await waitFor(() => expect(rows[0].querySelector(".bg-success\\/80")).not.toBeNull());
  });
});

describe("terminal path clicks", () => {
  it("open the file in the Folder tab instead of the OS when the IDE is mounted", async () => {
    renderHook(() => useExplorerPathRouting());
    act(() =>
      activateTerminalLink(new MouseEvent("click", { button: 0, ctrlKey: true }), "src/app.ts", { workspaceId: "w1" }),
    );
    expect(useIdeExplorerStore.getState().opened.files).toEqual({ workspaceId: "w1", path: "src/app.ts" });
    expect(useIdeSidePanelStore.getState()).toMatchObject({ open: true, active: "files" });
    expect(calls.some((url) => url.includes("terminal-target"))).toBe(false);
  });

  it("fall back to the OS for another workspace's pane", () => {
    renderHook(() => useExplorerPathRouting());
    activateTerminalLink(new MouseEvent("click", { button: 0, ctrlKey: true }), "src/app.ts", { workspaceId: "w9" });
    expect(useIdeExplorerStore.getState().opened.files).toBeNull();
    expect(calls.some((url) => url.includes("terminal-target"))).toBe(true);
  });
});
