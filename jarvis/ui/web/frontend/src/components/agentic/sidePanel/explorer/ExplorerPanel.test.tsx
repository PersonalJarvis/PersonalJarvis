import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { ExplorerPanel } from "./ExplorerPanel";
import { WORKSPACE_PATH_TYPE } from "@/components/agentic/paneDrop";
import { activateTerminalLink } from "@/lib/terminalLinks";
import { useEventStore } from "@/store/events";
import { useIdeChatStore } from "@/store/ideChat";
import { useCodeEditorStore } from "@/store/codeEditor";
import { parsePrintedPath, useExplorerPathRouting } from "@/store/ideExplorer";
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

const calls: { url: string; body: string }[] = [];

function respond(url: string): unknown {
  if (url.includes("/changes")) return CHANGES;
  if (url.includes("/text-file/version")) return { version: url.includes("src") ? null : "v1" };
  if (url.includes("/entries")) return { path: "src/new.ts", trashed: true };
  if (url.includes("/files")) {
    if (url.includes("?path=")) return { workspace_id: "w1", root_name: "app", path: "src", truncated: false, entries: [] };
    return { workspace_id: "w1", root_name: "app", path: "", truncated: false, entries: [
      { name: "src", path: "src", is_directory: true, is_symlink: false },
      { name: "README.md", path: "README.md", is_directory: false, is_symlink: false },
    ] };
  }
  return {};
}

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, body: String(init?.body ?? "") });
    return new Response(JSON.stringify(respond(url)), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  useEventStore.setState({ activeSection: "agentic-ide" });
  useIdeChatStore.setState({ workspace: { id: "w1", name: "App", path: "/code/app" }, stagedPane: null });
  useCodeEditorStore.setState({ tabs: [], active: {}, files: {}, visible: false, reveal: null, closed: [] });
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

  it("opens a changed file as a diff tab in the code editor", async () => {
    render(<ExplorerPanel view="changes" />);
    const [row] = await screen.findAllByTestId("explorer-change-row");
    fireEvent.click(row);
    const { tabs, visible } = useCodeEditorStore.getState();
    expect(tabs).toMatchObject([{ path: "src/app.ts", mode: "diff", preview: true }]);
    expect(visible).toBe(true);
  });

  it("shows the folder as a lazy tree in the Folder tab", async () => {
    render(<ExplorerPanel view="files" />);
    await waitFor(() => expect(screen.getAllByTestId("explorer-tree-row")).toHaveLength(2));
    const rows = screen.getAllByTestId("explorer-tree-row");
    expect(rows.map((row) => row.dataset.path)).toEqual(["src", "README.md"]);
    // The folder holds a change, so it carries the marker while collapsed.
    await waitFor(() => expect(rows[0].querySelector(".bg-success\\/80")).not.toBeNull());
  });

  it("opens a file as a preview tab on click and keeps it on double click", async () => {
    render(<ExplorerPanel view="files" />);
    await waitFor(() => expect(screen.getAllByTestId("explorer-tree-row")).toHaveLength(2));
    const readme = screen.getAllByTestId("explorer-tree-row")[1];
    fireEvent.click(readme);
    expect(useCodeEditorStore.getState().tabs).toMatchObject([{ path: "README.md", mode: "edit", preview: true }]);
    fireEvent.doubleClick(readme);
    expect(useCodeEditorStore.getState().tabs).toMatchObject([{ path: "README.md", preview: false }]);
  });

  it("creates a new file from the header and opens it", async () => {
    render(<ExplorerPanel view="files" />);
    await waitFor(() => expect(screen.getAllByTestId("explorer-tree-row")).toHaveLength(2));
    fireEvent.click(screen.getByTestId("explorer-new-file"));
    const field = await screen.findByTestId("explorer-name-field");
    fireEvent.change(field, { target: { value: "src/new.ts" } });
    fireEvent.keyDown(field, { key: "Enter" });
    await waitFor(() => expect(useCodeEditorStore.getState().tabs).toMatchObject([{ path: "src/new.ts", preview: false }]));
    const create = calls.find((call) => call.url.endsWith("/entries"));
    expect(JSON.parse(create!.body)).toEqual({ path: "src/new.ts", kind: "file" });
  });

  it("asks before deleting and sends the file to the trash", async () => {
    render(<ExplorerPanel view="files" />);
    await waitFor(() => expect(screen.getAllByTestId("explorer-tree-row")).toHaveLength(2));
    fireEvent.keyDown(screen.getAllByTestId("explorer-tree-row")[1], { key: "Delete" });
    expect(calls.some((call) => call.url.includes("/entries/delete"))).toBe(false);
    fireEvent.click(await screen.findByTestId("explorer-delete-confirm"));
    await waitFor(() => expect(calls.some((call) => call.url.includes("/entries/delete"))).toBe(true));
    const sent = calls.find((call) => call.url.includes("/entries/delete"));
    expect(JSON.parse(sent!.body)).toEqual({ path: "README.md", permanent: false });
  });
});

describe("terminal path clicks", () => {
  it("open a printed file in the editor at its line", async () => {
    renderHook(() => useExplorerPathRouting());
    act(() =>
      activateTerminalLink(new MouseEvent("click", { button: 0, ctrlKey: true }), "README.md:12:3", { workspaceId: "w1" }),
    );
    await waitFor(() => expect(useCodeEditorStore.getState().tabs).toMatchObject([{ path: "README.md", preview: false }]));
    expect(useCodeEditorStore.getState().reveal).toMatchObject({ line: 12, column: 3 });
    expect(calls.some((call) => call.url.includes("terminal-target"))).toBe(false);
  });

  it("show a printed folder in the Folder tab", async () => {
    renderHook(() => useExplorerPathRouting());
    act(() => activateTerminalLink(new MouseEvent("click", { button: 0, ctrlKey: true }), "src/", { workspaceId: "w1" }));
    await waitFor(() => expect(useIdeSidePanelStore.getState()).toMatchObject({ open: true, active: "files" }));
    expect(useCodeEditorStore.getState().tabs).toEqual([]);
  });

  it("fall back to the OS for another workspace's pane", () => {
    renderHook(() => useExplorerPathRouting());
    activateTerminalLink(new MouseEvent("click", { button: 0, ctrlKey: true }), "src/app.ts", { workspaceId: "w9" });
    expect(useCodeEditorStore.getState().tabs).toEqual([]);
    expect(calls.some((call) => call.url.includes("terminal-target"))).toBe(true);
  });
});

describe("parsePrintedPath", () => {
  it("reads line suffixes, absolute paths and file URIs", () => {
    expect(parsePrintedPath("/code/app", "src/a.ts:4:2")).toEqual({ path: "src/a.ts", line: 4, column: 2 });
    expect(parsePrintedPath("/code/app", "src/a.ts(7,1)")).toEqual({ path: "src/a.ts", line: 7, column: 1 });
    expect(parsePrintedPath("/code/app", "/code/app/src/a.ts#L9")).toEqual({ path: "src/a.ts", line: 9, column: undefined });
    expect(parsePrintedPath("C:\\Code\\App", "c:\\code\\app\\x.py:3")).toEqual({ path: "x.py", line: 3, column: undefined });
    expect(parsePrintedPath("/code/app", "file:///code/app/b.md")).toMatchObject({ path: "b.md" });
  });

  it("refuses paths outside the workspace", () => {
    expect(parsePrintedPath("/code/app", "/etc/passwd").path).toBeNull();
    expect(parsePrintedPath("/code/app", "../secret.txt").path).toBeNull();
    expect(parsePrintedPath("/code/app", "/code/application/x").path).toBeNull();
  });
});
