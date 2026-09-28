import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AgentsOverview } from "./AgentsOverview";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeProjectsStore } from "@/store/ideProjects";
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

describe("AgentsOverview", () => {
  it("lists only the active workspace agents with names and live dots", () => {
    render(<AgentsOverview />);
    const rows = screen.getAllByTestId("ide-workspace-agent-row");
    expect(rows.map((row) => row.getAttribute("data-pane"))).toEqual(["T1", "T2", "T3"]);
    expect(screen.getByTestId("ide-workspace-agents-count").textContent).toBe("3 agents");
    expect(rows[0].getAttribute("data-kind")).toBe("working");
    expect(rows[1].getAttribute("data-kind")).toBe("waiting");
    expect(rows[2].getAttribute("data-kind")).toBe("idle");
    expect(rows[0].textContent).toContain("T1");
    expect(rows[0].textContent).toContain("Claude Code");
    expect(rows[1].textContent).toContain("Codex");
  });

  it("marks errors red and idle panes gray", () => {
    useWorkspacePanesStore.setState({
      panes: [
        pane("T1", "w1", { status: "error", activity: "" }),
        pane("T9", "w1", { status: "live", activity: "", worked: false }),
      ],
    });
    render(<AgentsOverview />);
    const rows = screen.getAllByTestId("ide-workspace-agent-row");
    expect(rows[0].getAttribute("data-kind")).toBe("error");
    expect(rows[1].getAttribute("data-kind")).toBe("idle");
  });

  it("switches the list when the workspace tab changes", () => {
    const { rerender } = render(<AgentsOverview />);
    expect(screen.getAllByTestId("ide-workspace-agent-row")).toHaveLength(3);
    act(() => useIdeProjectsStore.setState({ activeWorkspaceId: "w2" }));
    rerender(<AgentsOverview />);
    const rows = screen.getAllByTestId("ide-workspace-agent-row");
    expect(rows.map((row) => row.getAttribute("data-pane"))).toEqual(["T1"]);
  });

  it("asks the IDE view to focus the pane on click", () => {
    render(<AgentsOverview />);
    fireEvent.click(screen.getByRole("button", { name: /T2, Codex/ }));
    expect(useIdeChatStore.getState().paneRequest).toMatchObject({ workspaceId: "w1", pane: "T2" });
  });

  it("highlights the staged pane", () => {
    useIdeChatStore.setState({ stagedPane: "T2" });
    render(<AgentsOverview />);
    const rows = screen.getAllByTestId("ide-workspace-agent-row");
    expect(rows[1].getAttribute("aria-current")).toBe("true");
    expect(rows[0].getAttribute("aria-current")).toBeNull();
  });

  it("shows an empty hint when the workspace has no agents", () => {
    useIdeProjectsStore.setState({ activeWorkspaceId: "w9" });
    render(<AgentsOverview />);
    expect(screen.queryByTestId("ide-workspace-agent-row")).toBeNull();
    expect(screen.getByTestId("ide-workspace-agents").textContent).toContain("No agents");
  });

  it("summarises the workspace by state, needs-input first", () => {
    render(<AgentsOverview />);
    const summary = screen.getByTestId("ide-agents-summary");
    const kinds = Array.from(summary.querySelectorAll("[data-kind]")).map((node) => node.getAttribute("data-kind"));
    expect(kinds).toEqual(["waiting", "working", "idle"]);
    expect(summary.textContent).toContain("1 need input");
    expect(summary.textContent).toContain("1 working");
  });

  it("shows each agent's state, its last task and its last output", () => {
    const now = Math.floor(Date.now() / 1000);
    useWorkspacePanesStore.setState({
      panes: [
        pane("T1", "w1", {
          activity: "working",
          activity_since: now - 180,
          last_output_at: now - 5,
          recap: "Fix the login test",
        }),
      ],
    });
    render(<AgentsOverview />);
    const row = screen.getByTestId("ide-workspace-agent-row");
    expect(screen.getByTestId("ide-agent-state").textContent).toBe("Working");
    expect(row.textContent).toContain("for 3m");
    expect(screen.getByTestId("ide-agent-task").textContent).toBe("Fix the login test");
    expect(row.textContent).toMatch(/Last output \d+s ago/);
  });
});
