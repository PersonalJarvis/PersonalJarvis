import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useIdeChatStore } from "@/store/ideChat";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IdeSidePanelFrame } from "./IdeSidePanel";
import { IdeSidePanelToggle } from "./IdeSidePanelToggle";
import { useEventStore } from "@/store/events";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { resetWorkspacePanesPoll, useWorkspacePanesStore } from "@/store/workspacePanes";
import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";

vi.mock("@/components/workspace/WorkspaceTerminal", () => ({
  WorkspaceTerminal: ({ paneKey, workspaceId, active }: { paneKey: string; workspaceId: string; active: boolean }) => (
    <div data-testid={`shell-${paneKey}`} data-workspace={workspaceId} data-active={String(active)} />
  ),
}));

// The real stage is a WebGL scene; the tab only owes it the right props and a place to live.
vi.mock("@/components/society/office/OfficeStage", () => ({
  OfficeStage: ({ onOpenLedger, initialFloor, compact }: { onOpenLedger: () => void; initialFloor?: string; compact?: boolean }) => (
    <div data-testid="office-stage" data-floor={initialFloor} data-compact={String(Boolean(compact))}>
      <button type="button" onClick={onOpenLedger}>ledger</button>
    </div>
  ),
}));

function Harness() {
  return (
    <>
      <IdeSidePanelToggle />
      <IdeSidePanelFrame>
        <div data-testid="grid">grid</div>
      </IdeSidePanelFrame>
    </>
  );
}

beforeEach(() => {
  localStorage.clear();
  resetWorkspacePanesPoll();
  useWorkspacePanesStore.setState({ panes: [], activeId: null, loaded: true, load: async () => {} });
  useIdeProjectsStore.setState({ activeWorkspaceId: null });
  useEventStore.setState({ activeSection: "agentic-ide" });
  useIdeSidePanelStore.setState({ open: false, tabs: ["agents"], active: "agents", terminals: [], maximized: false, inUse: false });
  useIdeChatStore.setState({ workspace: { id: "w1", name: "App", path: "/code/app" } });
});

afterEach(cleanup);

describe("IdeSidePanel", () => {
  it("opens independent shell tabs and retains them through switching and collapsing", () => {
    useIdeSidePanelStore.getState().setOpen(true);
    render(<Harness />);
    const add = () => {
      fireEvent.click(screen.getByTestId("ide-side-panel-add"));
      fireEvent.click(screen.getByTestId("ide-side-panel-add-terminal"));
    };
    add();
    const first = useIdeSidePanelStore.getState().terminals[0];
    const firstNode = screen.getByTestId(`shell-${first.id}`);
    expect(firstNode.dataset.workspace).toBe("w1");
    add();
    const second = useIdeSidePanelStore.getState().terminals[1];
    expect(first.id).not.toBe(second.id);
    expect(screen.getByRole("tab", { name: "Terminal 1" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Terminal 2" })).toBeTruthy();
    fireEvent.click(screen.getByTestId("ide-side-panel-tab-agents"));
    expect(screen.getByTestId(`shell-${first.id}`)).toBe(firstNode);
    fireEvent.click(screen.getByTestId("ide-side-panel-toggle"));
    expect(screen.getByTestId(`shell-${first.id}`)).toBe(firstNode);
    expect(firstNode.dataset.active).toBe("false");
    fireEvent.click(screen.getByTestId("ide-side-panel-toggle"));
    fireEvent.click(screen.getByTestId(`ide-side-panel-tab-${first.id}`));
    expect(firstNode.dataset.active).toBe("true");
    fireEvent.click(screen.getByTestId(`ide-side-panel-close-${second.id}`));
    expect(screen.queryByTestId(`shell-${second.id}`)).toBeNull();
    expect(screen.getByTestId(`shell-${first.id}`)).toBe(firstNode);
    // Session-only tabs must never respawn shells on a page reload.
    expect(localStorage.getItem("jarvis.agenticIde.sidePanelTabs.v5")).not.toContain("terminal:");
  });

  it("keeps terminals pinned to their workspace and offers another shell on every + click", () => {
    useIdeSidePanelStore.getState().addTerminalTab("w1", "App");
    render(<Harness />);
    const first = useIdeSidePanelStore.getState().terminals[0];
    const node = screen.getByTestId(`shell-${first.id}`);
    act(() => useIdeChatStore.setState({ workspace: { id: "w2", name: "Other", path: "/code/other" } }));
    expect(screen.queryByTestId(`ide-side-panel-tab-${first.id}`)).toBeNull();
    expect(node.dataset.active).toBe("false");
    fireEvent.click(screen.getByTestId("ide-side-panel-add"));
    fireEvent.click(screen.getByTestId("ide-side-panel-add-terminal"));
    expect(useIdeSidePanelStore.getState().terminals[1].workspaceId).toBe("w2");
    act(() => useIdeChatStore.setState({ workspace: { id: "w1", name: "App", path: "/code/app" } }));
    fireEvent.click(screen.getByTestId(`ide-side-panel-tab-${first.id}`));
    expect(screen.getByTestId(`shell-${first.id}`)).toBe(node);
    expect(node.dataset.active).toBe("true");
  });

  it("requires a workspace before starting a shell", () => {
    useIdeSidePanelStore.getState().setOpen(true);
    useIdeChatStore.setState({ workspace: null });
    render(<Harness />);
    fireEvent.click(screen.getByTestId("ide-side-panel-add"));
    expect((screen.getByTestId("ide-side-panel-add-terminal") as HTMLButtonElement).disabled).toBe(true);
  });

  it("opens and closes from the caption toggle alone", () => {
    render(<Harness />);
    expect(screen.queryByTestId("ide-side-panel")).toBeNull();
    expect(screen.getByTestId("ide-side-panel-host").style.width).toBe("0px");

    fireEvent.click(screen.getByTestId("ide-side-panel-toggle"));
    expect(screen.getByTestId("ide-side-panel-toggle").getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("ide-side-panel")).toBeTruthy();
    expect(screen.getByTestId("ide-side-panel-tab-agents").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("ide-workspace-agents")).toBeTruthy();
    expect(screen.getByTestId("ide-side-panel-resizer")).toBeTruthy();
    expect(localStorage.getItem("jarvis.agenticIde.sidePanelOpen")).toBe("1");

    fireEvent.click(screen.getByTestId("ide-side-panel-toggle"));
    expect(screen.queryByTestId("ide-side-panel")).toBeNull();
    expect(localStorage.getItem("jarvis.agenticIde.sidePanelOpen")).toBe("0");
  });

  it("keeps the grid mounted while the panel opens and closes", () => {
    render(<Harness />);
    const grid = screen.getByTestId("grid");
    fireEvent.click(screen.getByTestId("ide-side-panel-toggle"));
    fireEvent.click(screen.getByTestId("ide-side-panel-toggle"));
    expect(screen.getByTestId("grid")).toBe(grid);
  });

  it("stays open on the launcher when the last tab closes", () => {
    act(() => useIdeSidePanelStore.getState().setOpen(true));
    render(<Harness />);
    fireEvent.click(screen.getByTestId("ide-side-panel-close-agents"));
    expect(useIdeSidePanelStore.getState()).toMatchObject({ open: true, tabs: [] });
    expect(screen.getByTestId("ide-side-panel-launcher").textContent).toContain("Open a surface");
  });

  it("opens empty on the launcher and adds only the surface picked there", () => {
    useIdeSidePanelStore.setState({ tabs: [] });
    render(<Harness />);
    fireEvent.click(screen.getByTestId("ide-side-panel-toggle"));
    const launcher = screen.getByTestId("ide-side-panel-launcher");
    expect(launcher.textContent).toContain("Browser");
    expect(launcher.textContent).toContain("Terminal");
    fireEvent.click(screen.getByTestId("ide-side-panel-launch-files"));
    expect(useIdeSidePanelStore.getState()).toMatchObject({ tabs: ["files"], active: "files" });
    expect(screen.queryByTestId("ide-side-panel-launcher")).toBeNull();
  });

  it("opens a surface from its letter, but never while the reader types", () => {
    act(() => useIdeSidePanelStore.setState({ open: true, tabs: [], active: "agents" }));
    // A closed menu that stays mounted (hidden) must not mute the letters.
    render(<><textarea data-testid="composer" /><div role="menu" hidden /><Harness /></>);
    fireEvent.keyDown(screen.getByTestId("composer"), { key: "g" });
    expect(useIdeSidePanelStore.getState().tabs).toEqual([]);
    fireEvent.keyDown(document.body, { key: "g", ctrlKey: true });
    expect(useIdeSidePanelStore.getState().tabs).toEqual([]);
    fireEvent.keyDown(document.body, { key: "g" });
    expect(useIdeSidePanelStore.getState()).toMatchObject({ tabs: ["git"], active: "git" });
  });

  it("starts a terminal from T and greys it out without a workspace", () => {
    act(() => useIdeSidePanelStore.setState({ open: true, tabs: [], active: "agents" }));
    useIdeChatStore.setState({ workspace: null });
    render(<Harness />);
    const row = screen.getByTestId("ide-side-panel-launch-terminal");
    expect(row.getAttribute("aria-disabled")).toBe("true");
    expect(row.getAttribute("title")).toBe("Select a workspace to open a terminal.");
    fireEvent.keyDown(document.body, { key: "t" });
    expect(useIdeSidePanelStore.getState().terminals).toEqual([]);
    act(() => useIdeChatStore.setState({ workspace: { id: "w1", name: "App", path: "/code/app" } }));
    fireEvent.keyDown(document.body, { key: "t" });
    expect(useIdeSidePanelStore.getState().terminals).toHaveLength(1);
  });

  it("picks a surface by letter from the open + menu", () => {
    act(() => useIdeSidePanelStore.setState({ open: true, tabs: ["agents"], active: "agents" }));
    render(<Harness />);
    const add = screen.getByTestId("ide-side-panel-add");
    fireEvent.click(add);
    expect(screen.getByTestId("ide-side-panel-add-browser").textContent).toContain("B");
    fireEvent.keyDown(add, { key: "s" });
    expect(useIdeSidePanelStore.getState()).toMatchObject({ tabs: ["agents", "search"], active: "search" });
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("keeps the browser page loaded while another tab is in front", () => {
    localStorage.setItem("jarvis.agenticIde.browserUrl.v1", "http://localhost:5173/");
    act(() => useIdeSidePanelStore.setState({ open: true, tabs: ["browser", "agents"], active: "browser" }));
    render(<Harness />);
    const frame = screen.getByTestId("ide-browser-frame");
    expect(frame.getAttribute("src")).toBe("http://localhost:5173/");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-top-navigation");
    fireEvent.click(screen.getByTestId("ide-side-panel-tab-agents"));
    expect(screen.getByTestId("ide-browser-frame")).toBe(frame);
    fireEvent.change(screen.getByTestId("ide-browser-address"), { target: { value: "8080" } });
    fireEvent.submit(screen.getByTestId("ide-browser-address").closest("form")!);
    expect(screen.getByTestId("ide-browser-frame").getAttribute("src")).toBe("http://localhost:8080/");
    expect(localStorage.getItem("jarvis.agenticIde.browserUrl.v1")).toBe("http://localhost:8080/");
  });

  it("keeps + usable with every tab listed, open ones ticked, and focuses an open one", () => {
    act(() => useIdeSidePanelStore.setState({ open: true, tabs: ["agents", "changes", "files"], active: "agents" }));
    render(<Harness />);
    const add = screen.getByTestId("ide-side-panel-add") as HTMLButtonElement;
    expect(add.disabled).toBe(false);
    fireEvent.click(add);
    expect(screen.getByTestId("ide-side-panel-add-changes").getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByTestId("ide-side-panel-add-changes"));
    expect(useIdeSidePanelStore.getState()).toMatchObject({ active: "changes", tabs: ["agents", "changes", "files"] });
  });

  it("starts with Agents alone and adds Changes and Folder from +", () => {
    act(() => useIdeSidePanelStore.setState({ open: true, tabs: ["agents"], active: "agents" }));
    render(<Harness />);
    fireEvent.click(screen.getByTestId("ide-side-panel-add"));
    expect(screen.getByTestId("ide-side-panel-add-files").getAttribute("aria-checked")).toBe("false");
    fireEvent.click(screen.getByTestId("ide-side-panel-add-files"));
    expect(useIdeSidePanelStore.getState()).toMatchObject({ active: "files", tabs: ["agents", "files"] });
    expect(screen.getByTestId("ide-side-panel-tab-files")).toBeTruthy();
  });

  it("offers a closed tab again through +", () => {
    act(() => useIdeSidePanelStore.setState({ open: true, tabs: [], active: "agents" }));
    render(<Harness />);
    const add = screen.getByTestId("ide-side-panel-add") as HTMLButtonElement;
    expect(add.disabled).toBe(false);
    fireEvent.click(add);
    fireEvent.click(screen.getByTestId("ide-side-panel-add-agents"));
    expect(useIdeSidePanelStore.getState().tabs).toEqual(["agents"]);
    expect(screen.getByTestId("ide-side-panel-tab-agents")).toBeTruthy();
  });

  it("leaves nothing on the right edge while closed and reopens at the tab in front", () => {
    useIdeSidePanelStore.setState({ tabs: ["agents", "files"], active: "files" });
    render(<Harness />);
    expect(screen.queryByTestId("ide-side-panel-rail")).toBeNull();
    expect(screen.queryByRole("navigation")).toBeNull();
    fireEvent.click(screen.getByTestId("ide-side-panel-toggle"));
    expect(useIdeSidePanelStore.getState()).toMatchObject({ open: true, active: "files" });
  });

  it("puts a dot on the shut panel's toggle while an agent of the workspace waits", () => {
    const row = (name: string, workspaceId: string, activity: WorkspacePaneRow["activity"]) => ({
      workspace_id: workspaceId, workspace_name: workspaceId, folder: "/code/app", workspace_active: workspaceId === "w1",
      key: name, history_id: `${name}@${workspaceId}`, name, agent: "claude", display_name: "Claude Code",
      accepts_prompts: true, status: "live", exit_code: null, activity, activity_since: 0, worked: true,
      started_at: 1, last_output_at: 2, last_prompt: "", last_prompt_at: null, recap: "", has_resume: false,
      readable: true, account: null, account_label: null, archived: false,
    }) as WorkspacePaneRow;
    useIdeProjectsStore.setState({ activeWorkspaceId: "w1" });
    useWorkspacePanesStore.setState({ panes: [row("T1", "w1", "working"), row("T1", "w2", "asking")] });
    render(<Harness />);
    expect(screen.queryByTestId("ide-side-panel-waiting")).toBeNull();
    act(() => useWorkspacePanesStore.setState({ panes: [row("T1", "w1", "asking"), row("T2", "w1", "asking")] }));
    expect(screen.getByTestId("ide-side-panel-waiting")).toBeTruthy();
    expect(screen.getByTestId("ide-side-panel-toggle").getAttribute("aria-label")).toBe("Open side panel (2 waiting for you)");
    // The open panel lists the agents itself.
    fireEvent.click(screen.getByTestId("ide-side-panel-toggle"));
    expect(screen.queryByTestId("ide-side-panel-waiting")).toBeNull();
  });

  it("adds the Jarvis Verse tab from + and shows the coding floor in a compact stage", async () => {
    act(() => useIdeSidePanelStore.setState({ open: true, tabs: ["agents"], active: "agents" }));
    render(<Harness />);
    fireEvent.click(screen.getByTestId("ide-side-panel-add"));
    const item = screen.getByTestId("ide-side-panel-add-office");
    expect(item.textContent).toContain("Jarvis Verse");
    expect(item.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(item);
    expect(useIdeSidePanelStore.getState()).toMatchObject({ active: "office", tabs: ["agents", "office"] });
    expect(screen.getByTestId("ide-side-panel-tab-office").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("ide-office-tab").className).toContain("h-full");
    const stage = await screen.findByTestId("office-stage");
    expect(stage.getAttribute("data-floor")).toBe("coding");
    expect(stage.getAttribute("data-compact")).toBe("true");
  });

  it("widens a narrow panel for the office and sends the ledger action to Agents", async () => {
    localStorage.setItem("jarvis.agenticIde.sidePanelWidth.v1", "300");
    act(() => useIdeSidePanelStore.setState({ open: true, tabs: ["office"], active: "office" }));
    render(<Harness />);
    expect(screen.getByTestId("ide-side-panel-host").style.width).toBe("520px");
    fireEvent.click(await screen.findByText("ledger"));
    expect(useIdeSidePanelStore.getState()).toMatchObject({ active: "agents", tabs: ["office", "agents"] });
  });

  it("keeps a stored width wider than the old 720px cap", () => {
    localStorage.setItem("jarvis.agenticIde.sidePanelWidth.v1", "1100");
    act(() => useIdeSidePanelStore.setState({ open: true, tabs: ["agents"], active: "agents" }));
    render(<Harness />);
    expect(screen.getByTestId("ide-side-panel-host").style.width).toBe("1100px");
  });

  it("maximizes the office over the whole view without remounting it or resizing the grid", async () => {
    localStorage.setItem("jarvis.agenticIde.sidePanelWidth.v1", "600");
    act(() => useIdeSidePanelStore.setState({ open: true, tabs: ["office"], active: "office" }));
    render(<Harness />);
    const stage = await screen.findByTestId("office-stage");
    const host = screen.getByTestId("ide-side-panel-host");

    fireEvent.click(screen.getByTestId("ide-side-panel-maximize"));
    expect(useIdeSidePanelStore.getState().maximized).toBe(true);
    expect(screen.getByTestId("ide-side-panel-body").className).toContain("absolute inset-0");
    expect(screen.getByTestId("ide-side-panel-grid").className).toContain("invisible");
    expect(screen.getByTestId("grid")).toBeTruthy();
    expect(host.className).not.toContain("relative");
    expect(host.style.width).toBe("600px");
    expect(screen.queryByTestId("ide-side-panel-resizer")).toBeNull();
    expect(screen.getByTestId("office-stage")).toBe(stage);

    fireEvent.click(screen.getByTestId("ide-side-panel-maximize"));
    expect(screen.getByTestId("ide-side-panel-body").className).toBe("relative h-full");
    expect(screen.getByTestId("ide-side-panel-grid").className).not.toContain("invisible");
    expect(screen.getByTestId("office-stage")).toBe(stage);
  });

  it("drops the full view when the panel closes", () => {
    act(() => useIdeSidePanelStore.setState({ open: true, tabs: ["agents"], active: "agents" }));
    render(<Harness />);
    fireEvent.click(screen.getByTestId("ide-side-panel-maximize"));
    fireEvent.click(screen.getByTestId("ide-side-panel-toggle"));
    expect(useIdeSidePanelStore.getState().maximized).toBe(false);
  });

  it("frames the panel in blue while the reader works in it", () => {
    useIdeSidePanelStore.setState({ open: true });
    const { rerender } = render(<IdeSidePanelFrame markInUse><div data-testid="grid">grid</div></IdeSidePanelFrame>);
    expect(screen.queryByTestId("ide-side-panel-in-use")).toBeNull();
    fireEvent.pointerDown(screen.getByTestId("ide-side-panel-tab-agents"));
    expect(useIdeSidePanelStore.getState().inUse).toBe(true);
    const ring = screen.getByTestId("ide-side-panel-in-use");
    expect(ring.className).toContain("ring-accent");
    expect(ring.className).toContain("pointer-events-none");
    // A press on the grid is the grid's business (the pane hands the frame back).
    fireEvent.pointerDown(screen.getByTestId("grid"));
    expect(useIdeSidePanelStore.getState().inUse).toBe(true);
    act(() => useIdeSidePanelStore.getState().setInUse(false));
    expect(screen.queryByTestId("ide-side-panel-in-use")).toBeNull();
    // The classic pane style draws no blue frames at all.
    act(() => useIdeSidePanelStore.getState().setInUse(true));
    rerender(<IdeSidePanelFrame><div data-testid="grid">grid</div></IdeSidePanelFrame>);
    expect(screen.queryByTestId("ide-side-panel-in-use")).toBeNull();
    // Closing the panel ends its turn as the area in use.
    act(() => useIdeSidePanelStore.getState().setOpen(false));
    expect(useIdeSidePanelStore.getState().inUse).toBe(false);
  });
});
