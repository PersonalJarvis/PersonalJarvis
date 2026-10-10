import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PaneChangesDialog } from "./PaneChangesDialog";
import { changeTotals, clearPaneChangesCache, filesWrittenBy, prefetchPaneChanges } from "./paneChangesApi";
import { useCodeEditorStore } from "@/store/codeEditor";
import en from "@/i18n/locales/pane_review/en.json";
import de from "@/i18n/locales/pane_review/de.json";
import es from "@/i18n/locales/pane_review/es.json";
import pt from "@/i18n/locales/pane_review/pt.json";

const author = (pane: string) => ({ pane, history_id: `h-${pane}`, agent: "claude", display_name: "Claude", last_edit_ms: 1 });

const CHANGES = {
  workspace_id: "w1",
  available: true,
  branch: "main",
  truncated: false,
  reason: "",
  files: [
    { path: "src/app.ts", status: "modified", added: 3, removed: 1, is_directory: false, authors: [author("T1")] },
    { path: "src/other.ts", status: "modified", added: 7, removed: 0, is_directory: false, authors: [author("T2")] },
    { path: "notes.md", status: "untracked", added: 2, removed: 0, is_directory: false, authors: [] },
  ],
};

function diffFor(path: string) {
  return {
    workspace_id: "w1",
    path,
    status: "modified",
    binary: false,
    added: 1,
    removed: 1,
    truncated: false,
    hunks: [{
      header: "@@ -1 +1 @@",
      lines: [
        { kind: "del", text: `old ${path}`, old_no: 1, new_no: null },
        { kind: "add", text: `new ${path}`, old_no: null, new_no: 1 },
      ],
    }],
  };
}

const urls: string[] = [];
/** False plays a backend from before the pane route: FastAPI answers 404 "Not Found". */
let paneRoute = true;

/** True: the pane route sends every diff inline, as the current backend does. */
let inlineDiffs = false;

beforeEach(() => {
  urls.length = 0;
  paneRoute = true;
  inlineDiffs = false;
  clearPaneChangesCache();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    urls.push(url);
    const path = new URL(url, "http://local").searchParams.get("path") ?? "";
    const pane = url.match(/\/terminals\/([^/]+)\/changes/)?.[1];
    if (pane && !paneRoute) {
      return new Response(JSON.stringify({ detail: "Not Found" }), { status: 404, headers: { "Content-Type": "application/json" } });
    }
    const body = url.includes("/diff")
      ? diffFor(path)
      : pane
        ? {
          ...CHANGES,
          base: "abc1234",
          generated: 412,
          since_ms: 1_700_000_000_000,
          // The pane route answers committed work too, and marks it.
          files: CHANGES.files
            .filter((file) => file.authors.some((a) => a.pane === pane))
            .map((file) => ({ ...file, committed: true, diff: inlineDiffs ? diffFor(file.path) : null })),
        }
        : url.includes("/changes") ? CHANGES : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  useCodeEditorStore.setState({ tabs: [], active: {}, files: {}, visible: false, reveal: null, closed: [], diffBases: {} });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const paths = () => screen.getAllByTestId("pane-changes-file").map((card) => card.dataset.path);

describe("paneChangesApi", () => {
  it("keeps only the files whose agent record names the pane", () => {
    expect(filesWrittenBy(CHANGES.files as never, "T1").map((file) => file.path)).toEqual(["src/app.ts"]);
    expect(filesWrittenBy(CHANGES.files as never, "T9")).toEqual([]);
  });

  it("adds up the line counts and skips unknown ones", () => {
    expect(changeTotals([...CHANGES.files, { path: "bin", status: "added", added: null, removed: null, is_directory: false }] as never))
      .toEqual({ added: 12, removed: 1 });
  });
});

describe("PaneChangesDialog", () => {
  it("lists only this pane's files and shows each one's diff", async () => {
    render(<PaneChangesDialog open onOpenChange={() => {}} workspaceId="w1" pane="T1" />);
    await screen.findAllByTestId("pane-changes-file");
    expect(paths()).toEqual(["src/app.ts"]);
    expect(await screen.findByText("new src/app.ts")).toBeTruthy();
    expect(screen.getByTestId("pane-changes-summary").textContent).toContain("1 file");
    expect(urls.some((url) => url.includes("/terminals/T1/diff") && url.includes("src%2Fapp.ts"))).toBe(true);
    expect(urls.some((url) => url.includes("other.ts"))).toBe(false);
  });

  it("asks the pane route, not the whole workspace, for the pane's own files", async () => {
    render(<PaneChangesDialog open onOpenChange={() => {}} workspaceId="w1" pane="T1" />);
    await screen.findAllByTestId("pane-changes-file");
    expect(urls[0]).toContain("/api/agentic-ide/workspaces/w1/terminals/T1/changes");
    expect(urls.some((url) => url.endsWith("/workspaces/w1/changes"))).toBe(false);
  });

  it("falls back to filtering the workspace list on a backend without the pane route", async () => {
    paneRoute = false;
    render(<PaneChangesDialog open onOpenChange={() => {}} workspaceId="w1" pane="T1" />);
    await screen.findAllByTestId("pane-changes-file");
    expect(paths()).toEqual(["src/app.ts"]);
    expect(urls.some((url) => url.endsWith("/workspaces/w1/changes"))).toBe(true);
  });

  it("paints the diffs the list carried without asking for them one by one", async () => {
    inlineDiffs = true;
    render(<PaneChangesDialog open onOpenChange={() => {}} workspaceId="w1" pane="T1" />);
    expect(await screen.findByText("new src/app.ts")).toBeTruthy();
    expect(urls.filter((url) => url.includes("/diff"))).toEqual([]);
  });

  it("opens already painted after a hover read the changes ahead", async () => {
    inlineDiffs = true;
    prefetchPaneChanges({ workspaceId: "w1", pane: "T1" });
    await waitFor(() => expect(urls.length).toBe(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    render(<PaneChangesDialog open onOpenChange={() => {}} workspaceId="w1" pane="T1" />);
    expect(await screen.findByText("new src/app.ts")).toBeTruthy();
    // The fresh reading is reused, not read a second time.
    expect(urls.filter((url) => url.includes("/terminals/T1/changes")).length).toBe(1);
    expect(screen.queryByText("Reading the changes…")).toBeNull();
  });

  it("widens to the whole folder on request", async () => {
    render(<PaneChangesDialog open onOpenChange={() => {}} workspaceId="w1" pane="T1" />);
    await screen.findAllByTestId("pane-changes-file");
    fireEvent.click(screen.getByTestId("pane-changes-scope-folder"));
    await waitFor(() => expect(paths()).toEqual(["src/app.ts", "src/other.ts", "notes.md"]));
  });

  it("says so when the agent has nothing uncommitted", async () => {
    render(<PaneChangesDialog open onOpenChange={() => {}} workspaceId="w1" pane="T9" />);
    expect(await screen.findByTestId("pane-changes-empty")).toBeTruthy();
  });

  it("shows committed work and diffs it against the base the list answered", async () => {
    render(<PaneChangesDialog open onOpenChange={() => {}} workspaceId="w1" pane="T1" />);
    const [card] = await screen.findAllByTestId("pane-changes-file");
    expect(within(card).getByTestId("pane-changes-commit-state").textContent).toBe("Committed");
    await screen.findByText("new src/app.ts");
    expect(urls.some((url) => url.includes("/terminals/T1/diff?path=src%2Fapp.ts&base=abc1234"))).toBe(true);
    expect(screen.getByText(/compared with the code before its first edit/)).toBeTruthy();
    expect(screen.getByTestId("pane-changes-generated").textContent).toBe("+ 412 generated files (build output), not listed");
  });

  it("asks the pane route for a worktree pane too", async () => {
    render(<PaneChangesDialog open onOpenChange={() => {}} workspaceId="w1" pane="T1" folder="/code/app-wt" branch="feat/x" />);
    await screen.findAllByTestId("pane-changes-file");
    expect(urls[0]).toContain("/workspaces/w1/terminals/T1/changes");
    expect(screen.getByText("feat/x")).toBeTruthy();
    expect(screen.queryByTestId("pane-changes-scope-folder")).toBeNull();
  });

  it("reads a worktree pane's own checkout through the folder routes on an older backend", async () => {
    paneRoute = false;
    render(<PaneChangesDialog open onOpenChange={() => {}} workspaceId="w1" pane="T3" folder="/code/app-wt" branch="feat/x" />);
    await screen.findAllByTestId("pane-changes-file");
    // Every change in its own checkout is its own, record or not.
    expect(paths()).toEqual(["src/app.ts", "src/other.ts", "notes.md"]);
    expect(urls[1]).toContain("/api/agentic-ide/git/changes?folder=%2Fcode%2Fapp-wt");
    await waitFor(() => expect(urls.filter((url) => url.includes("/api/agentic-ide/git/diff?folder=")).length).toBe(3));
    expect(screen.queryByTestId("pane-changes-scope-folder")).toBeNull();
    expect(screen.queryByLabelText("Open in the editor")).toBeNull();
  });

  it("opens a file in the code editor and closes the review", async () => {
    const onOpenChange = vi.fn();
    render(<PaneChangesDialog open onOpenChange={onOpenChange} workspaceId="w1" pane="T1" />);
    const [card] = await screen.findAllByTestId("pane-changes-file");
    fireEvent.click(await within(card).findByLabelText("Open in the editor"));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(useCodeEditorStore.getState().tabs.map((tab) => tab.path)).toEqual(["src/app.ts"]);
  });

  it("opens a file in the editor when its name or its row in the list is clicked, diffed against the review base", async () => {
    const onOpenChange = vi.fn();
    render(<PaneChangesDialog open onOpenChange={onOpenChange} workspaceId="w1" pane="T1" />);
    fireEvent.click(await screen.findByTestId("pane-changes-file-name"));
    const state = useCodeEditorStore.getState();
    expect(state.tabs.map((tab) => [tab.path, tab.mode])).toEqual([["src/app.ts", "diff"]]);
    expect(Object.values(state.diffBases)).toEqual([{ ref: "abc1234", label: "Changes by T1" }]);
    useCodeEditorStore.setState({ tabs: [], active: {}, files: {}, diffBases: {} });
    fireEvent.click(screen.getByTestId("pane-changes-list-item"));
    expect(useCodeEditorStore.getState().tabs.map((tab) => tab.path)).toEqual(["src/app.ts"]);
    expect(onOpenChange).toHaveBeenCalledTimes(2);
  });

  it("folds a file's diff away", async () => {
    render(<PaneChangesDialog open onOpenChange={() => {}} workspaceId="w1" pane="T1" />);
    expect(await screen.findByText("new src/app.ts")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Collapse this file"));
    expect(screen.queryByText("new src/app.ts")).toBeNull();
  });
});

describe("pane review i18n parity", () => {
  const keys = (value: Record<string, unknown>, prefix = ""): string[] =>
    Object.entries(value).flatMap(([key, nested]) => nested && typeof nested === "object"
      ? keys(nested as Record<string, unknown>, `${prefix}${key}.`)
      : [`${prefix}${key}`]).sort();

  for (const [language, locale] of Object.entries({ de, es, pt })) {
    it(`${language} has the same keys as en`, () => {
      expect(keys(locale)).toEqual(keys(en));
    });
  }
});
