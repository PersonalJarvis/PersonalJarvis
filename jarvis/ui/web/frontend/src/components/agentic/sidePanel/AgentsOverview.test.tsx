import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AgentsOverview } from "./AgentsOverview";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { usePaneRecapsStore } from "@/store/paneRecaps";
import { markPaneReviewed, usePaneReviewsStore } from "@/store/paneReviews";
import type { TerminalRecap } from "@/lib/agenticIdeApi";
import { resetWorkspacePanesPoll, useWorkspacePanesStore } from "@/store/workspacePanes";
import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";

function pane(name: string, workspaceId: string, overrides: Partial<WorkspacePaneRow> = {}): WorkspacePaneRow {
  return {
    workspace_id: workspaceId,
    workspace_name: workspaceId,
    folder: "/code/app",
    workspace_active: workspaceId === "w1",
    key: name,
    history_id: `${name}@${workspaceId}`,
    name,
    agent: "claude",
    display_name: "Claude Code",
    accepts_prompts: true,
    status: "live",
    exit_code: null,
    activity: "working",
    activity_since: 0,
    worked: true,
    started_at: 1,
    last_output_at: 2,
    last_prompt: "",
    last_prompt_at: null,
    recap: "",
    has_resume: false,
    readable: true,
    account: null,
    account_label: null,
    archived: false,
    ...overrides,
  };
}

beforeEach(() => {
  resetWorkspacePanesPoll();
  useIdeSidePanelStore.setState({ spotlight: null });
  usePaneReviewsStore.setState({ reviewed: {} });
  usePaneRecapsStore.setState({ workspaceId: null, byName: {}, load: async () => {} });
  useWorkspacePanesStore.setState({
    panes: [
      pane("T1", "w1", { activity: "working" }),
      pane("T2", "w1", { activity: "asking", agent: "codex", display_name: "Codex" }),
      pane("T3", "w1", { activity: "waiting", agent: "opencode", display_name: "opencode" }),
      pane("T1", "w2", { activity: "working" }),
    ],
    activeId: "w1",
    loaded: true,
    load: async () => {},
  });
  useIdeProjectsStore.setState({
    projects: [],
    activeWorkspaceId: "w1",
    pendingWorkspaceId: null,
    refreshRequest: null,
    action: null,
  });
  useIdeChatStore.setState({
    paneRequest: null,
    stagedPane: null,
    workspaces: [],
    agents: [],
    terminalRequest: null,
    newChatRequest: null,
    workspaceRequest: null,
    sessionRequest: null,
    addWorkspaceRequest: null,
  });
});

afterEach(cleanup);

const column = (id: string) => screen.getByTestId(`ide-agents-column-${id}`);
const panesIn = (id: string) =>
  Array.from(column(id).querySelectorAll('[data-testid="ide-workspace-agent-row"]')).map((row) =>
    row.getAttribute("data-pane"),
  );

describe("AgentsOverview", () => {
  it("sorts the active workspace's agents into Done, Working and Reviewed", () => {
    render(<AgentsOverview />);
    // T1 works; T2 asks and T3 finished — both need a look; w2's T1 is not here.
    expect(panesIn("working")).toEqual(["T1"]);
    expect(panesIn("done").sort()).toEqual(["T2", "T3"]);
    expect(panesIn("reviewed")).toEqual([]);
    expect(screen.getByTestId("ide-agents-column-count-done").textContent).toBe("2");
    const order = Array.from(document.querySelectorAll("[data-testid^='ide-agents-column-']"))
      .map((node) => node.getAttribute("data-testid"))
      .filter((id) => id && !id.includes("count"));
    expect(order).toEqual(["ide-agents-column-done", "ide-agents-column-working", "ide-agents-column-reviewed"]);
  });

  it("shows the brand mark, the goal and the live state on each card", () => {
    usePaneRecapsStore.setState({
      workspaceId: "w1",
      byName: { T2: { recap: "Login flow — flaky tests", source: "model" } as TerminalRecap },
    });
    render(<AgentsOverview />);
    const t2 = column("done").querySelector('[data-pane="T2"]')!;
    expect(t2.querySelector('[data-testid="ide-agent-title"]')?.textContent).toBe("Login flow — flaky tests");
    expect(t2.querySelector('[data-testid="agent-mark-codex"]')).not.toBeNull();
    expect(t2.getAttribute("data-kind")).toBe("waiting");
    const t1 = column("working").querySelector('[data-pane="T1"]')!;
    expect(t1.querySelector('[data-testid="ide-agent-state"]')?.textContent).toContain("Working");
  });

  it("moves a finished agent to Reviewed when its card is clicked, and focuses its pane", () => {
    render(<AgentsOverview />);
    fireEvent.click(column("done").querySelector('[data-pane="T3"]')!);
    expect(panesIn("reviewed")).toEqual(["T3"]);
    expect(useIdeChatStore.getState().paneRequest).toMatchObject({ workspaceId: "w1", pane: "T3" });
    expect(useIdeSidePanelStore.getState().spotlight).toEqual({ workspaceId: "w1", pane: "T3" });
  });

  it("counts a click on the pane in the grid as a review too", () => {
    render(<AgentsOverview />);
    act(() => markPaneReviewed("w1", "T2"));
    expect(panesIn("reviewed")).toEqual(["T2"]);
  });

  it("puts a reviewed agent back under Done once it finishes a newer job", () => {
    usePaneReviewsStore.setState({ reviewed: { "T3@w1": 100 } });
    useWorkspacePanesStore.setState({
      panes: [pane("T3", "w1", { activity: "waiting", activity_since: 50 })],
    });
    const { rerender } = render(<AgentsOverview />);
    expect(panesIn("reviewed")).toEqual(["T3"]);
    act(() =>
      useWorkspacePanesStore.setState({ panes: [pane("T3", "w1", { activity: "waiting", activity_since: 200 })] }),
    );
    rerender(<AgentsOverview />);
    expect(panesIn("done")).toEqual(["T3"]);
  });

  it("keeps an agent nobody ever tasked out of Done", () => {
    useWorkspacePanesStore.setState({ panes: [pane("T9", "w1", { activity: "waiting", worked: false })] });
    render(<AgentsOverview />);
    expect(panesIn("reviewed")).toEqual(["T9"]);
    expect(panesIn("done")).toEqual([]);
  });

  it("marks errors red", () => {
    useWorkspacePanesStore.setState({ panes: [pane("T1", "w1", { status: "error", activity: "" })] });
    render(<AgentsOverview />);
    const row = column("done").querySelector('[data-pane="T1"]')!;
    expect(row.getAttribute("data-kind")).toBe("error");
  });

  it("switches with the workspace tab", () => {
    const { rerender } = render(<AgentsOverview />);
    act(() => useIdeProjectsStore.setState({ activeWorkspaceId: "w2" }));
    rerender(<AgentsOverview />);
    expect(panesIn("working")).toEqual(["T1"]);
    expect(panesIn("done")).toEqual([]);
  });

  it("marks the card whose pane is spotlit", () => {
    useIdeSidePanelStore.setState({ spotlight: { workspaceId: "w1", pane: "T2" } });
    render(<AgentsOverview />);
    expect(column("done").querySelector('[data-pane="T2"]')?.getAttribute("aria-current")).toBe("true");
    expect(column("working").querySelector('[data-pane="T1"]')?.getAttribute("aria-current")).toBeNull();
  });

  it("shows an empty hint when the workspace has no agents", () => {
    useIdeProjectsStore.setState({ activeWorkspaceId: "w9" });
    render(<AgentsOverview />);
    expect(screen.queryByTestId("ide-workspace-agent-row")).toBeNull();
    expect(screen.getByTestId("ide-workspace-agents").textContent).toContain("No agents");
  });
});
