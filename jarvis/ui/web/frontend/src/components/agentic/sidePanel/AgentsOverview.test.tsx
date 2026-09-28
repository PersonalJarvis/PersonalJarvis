import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { AgentsOverview } from "./AgentsOverview";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { usePaneRecapsStore } from "@/store/paneRecaps";
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

describe("AgentsOverview", () => {
  it("titles a card with the model's short goal instead of the call-sign", () => {
    usePaneRecapsStore.setState({
      workspaceId: "w1",
      byName: { T2: { recap: "Login flow — flaky tests", source: "model" } as TerminalRecap },
    });
    render(<AgentsOverview />);
    const rows = screen.getAllByTestId("ide-workspace-agent-row");
    expect(rows[1].querySelector('[data-testid="ide-agent-title"]')?.textContent).toBe("Login flow — flaky tests");
    expect(rows[1].textContent).not.toContain("T2");
  });

  it("lists only the active workspace agents with names and live dots", () => {
    render(<AgentsOverview />);
    const rows = screen.getAllByTestId("ide-workspace-agent-row");
    expect(rows.map((row) => row.getAttribute("data-pane"))).toEqual(["T1", "T2", "T3"]);
    expect(screen.getByTestId("ide-workspace-agents-count").textContent).toBe("3 agents");
    expect(rows[0].getAttribute("data-kind")).toBe("working");
    expect(rows[1].getAttribute("data-kind")).toBe("waiting");
    expect(rows[2].getAttribute("data-kind")).toBe("idle");
    expect(rows[0].textContent).toContain("Claude Code");
    expect(rows[0].querySelector('[data-testid="agent-mark-claude"]')).not.toBeNull();
    expect(rows[1].querySelector('[data-testid="agent-mark-codex"]')).not.toBeNull();
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
    expect(useIdeSidePanelStore.getState().spotlight).toEqual({ workspaceId: "w1", pane: "T2" });
  });

  it("marks the card whose pane is spotlit", () => {
    useIdeSidePanelStore.setState({ spotlight: { workspaceId: "w1", pane: "T2" } });
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
    expect(screen.getByTestId("ide-agent-state").textContent).toBe("Working · 3m");
    expect(screen.getByTestId("ide-agent-title").textContent).toBe("Fix the login test");
    expect(row.textContent).toMatch(/Last output \d+s ago/);
  });
});

describe("stopping every agent", () => {
  it("asks first, then stops the runtime and refreshes the workspace list", async () => {
    const calls: string[] = [];
    const realFetch = globalThis.fetch;
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${String(input)}`);
      return new Response(JSON.stringify({ ok: true, closed_workspaces: 2 }), { status: 200 });
    }) as typeof fetch;
    try {
      render(<AgentsOverview />);
      fireEvent.click(screen.getByTestId("ide-agents-stop-all"));
      // Nothing is stopped by the first click — it only asks.
      expect(calls).toEqual([]);
      expect(screen.getByRole("alertdialog")).toBeTruthy();
      await act(async () => {
        fireEvent.click(screen.getByTestId("ide-agents-stop-confirm"));
      });
      expect(calls).toEqual(["POST /api/agentic-ide/runtime/stop"]);
      expect(useIdeProjectsStore.getState().refreshRequest).not.toBeNull();
      expect(screen.queryByRole("alertdialog")).toBeNull();
    } finally {
      globalThis.fetch = realFetch;
    }
  });
});
