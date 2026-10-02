import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentsOverview } from "./AgentsOverview";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";
import { usePaneRecapsStore } from "@/store/paneRecaps";
import { markPaneReviewed, usePaneReviewsStore } from "@/store/paneReviews";
import type { TerminalRecap } from "@/lib/agenticIdeApi";
import { resetWorkspacePanesPoll, useWorkspacePanesStore } from "@/store/workspacePanes";
import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";
import type { AgentSearchRequest, AgentSearchResponse } from "./agentSearch";

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
  localStorage.removeItem("jarvis.agenticIde.agentsScope");
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

  it("widens to every workspace of the folder and remembers the choice", () => {
    useWorkspacePanesStore.setState({
      panes: [
        pane("T1", "w1", { activity: "working" }),
        pane("T1", "w2", { activity: "working", workspace_name: "Blog" }),
        pane("T1", "w3", { activity: "working", folder: "/code/other" }),
      ],
    });
    const { unmount } = render(<AgentsOverview />);
    expect(screen.getAllByTestId("ide-workspace-agent-row")).toHaveLength(1);

    fireEvent.click(screen.getByTestId("ide-agents-scope-folder"));
    const rows = screen.getAllByTestId("ide-workspace-agent-row");
    expect(rows.map((row) => row.getAttribute("data-workspace"))).toEqual(["w1", "w2"]);
    expect(rows[1].textContent).toContain("Blog");
    expect(rows[0].textContent).not.toContain("w1");
    unmount();

    render(<AgentsOverview />);
    expect(screen.getByTestId("ide-agents-scope-folder").getAttribute("aria-checked")).toBe("true");
    expect(screen.getAllByTestId("ide-workspace-agent-row")).toHaveLength(2);
  });

  it("brings another workspace's pane forward from the folder view", () => {
    render(<AgentsOverview />);
    fireEvent.click(screen.getByTestId("ide-agents-scope-folder"));
    const row = screen
      .getAllByTestId("ide-workspace-agent-row")
      .find((entry) => entry.getAttribute("data-workspace") === "w2")!;
    fireEvent.click(row);
    expect(useIdeChatStore.getState().paneRequest).toMatchObject({ workspaceId: "w2", pane: "T1" });
  });
});

describe("AgentsOverview hybrid search", () => {
  class SearchWorker {
    static latest: SearchWorker;
    messages: AgentSearchRequest[] = [];
    onmessage: ((event: MessageEvent<AgentSearchResponse>) => void) | null = null;
    constructor() { SearchWorker.latest = this; }
    postMessage(message: AgentSearchRequest) { this.messages.push(message); }
    terminate() {}
    reply(response: Omit<AgentSearchResponse, "id"> & { matches?: { id: string; score: number }[] }) {
      this.onmessage?.({ data: { ...response, id: this.messages.at(-1)!.id } } as MessageEvent<AgentSearchResponse>);
    }
  }
  beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal("Worker", SearchWorker); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
  const search = () => {
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "fix expired sign-ins" } });
    act(() => vi.advanceTimersByTime(300));
    return SearchWorker.latest;
  };

  it("shows keyword and typo hits immediately without waiting for the model", () => {
    useWorkspacePanesStore.setState({ panes: [
      pane("T1", "w1", { recap: "OAuth reconnect" }),
      pane("T2", "w1", { recap: "Security audit" }),
    ] });
    render(<AgentsOverview />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "OAuth" } });
    expect(screen.getByTestId("ide-workspace-agent-row").getAttribute("data-pane")).toBe("T1");
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "securty" } });
    expect(screen.getByTestId("ide-workspace-agent-row").getAttribute("data-pane")).toBe("T2");
  });

  it("keeps concrete results visible when related tasks are loading or unavailable", () => {
    useWorkspacePanesStore.setState({ panes: [pane("T1", "w1", { recap: "Renew expired logins" })] });
    render(<AgentsOverview />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "expired logins" } });
    expect(screen.getByTestId("ide-workspace-agent-row")).toBeTruthy();
    act(() => vi.advanceTimersByTime(300));
    act(() => SearchWorker.latest.reply({ type: "loading" }));
    expect(screen.getByTestId("ide-workspace-agent-row")).toBeTruthy();
    act(() => SearchWorker.latest.reply({ type: "error" }));
    expect(screen.getByTestId("ide-workspace-agent-row")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("Word search still works");
    fireEvent.click(screen.getByTestId("ide-workspace-agent-row"));
    expect(useIdeChatStore.getState().paneRequest).toMatchObject({ workspaceId: "w1", pane: "T1" });
  });

  it("searches task text within the chosen scope and ranks across status groups", () => {
    useWorkspacePanesStore.setState({ panes: [
      pane("T1", "w1", { recap: "Login", last_prompt: "Renew tokens", activity: "working" }),
      pane("T2", "w1", { recap: "Plugin auth", activity: "waiting" }),
      pane("T1", "w2", { recap: "External login" }),
    ] });
    render(<AgentsOverview />);
    const worker = search();
    expect(worker.messages.at(-1)).toMatchObject({ documents: [
      { id: "T1@w1", texts: ["Login", "Renew tokens"] },
      { id: "T2@w1", texts: ["Plugin auth"] },
    ] });
    act(() => worker.reply({ type: "result", matches: [{ id: "T1@w1", score: 0.9 }, { id: "T2@w1", score: 0.8 }] }));
    expect(screen.getAllByTestId("ide-workspace-agent-row").map((row) => row.getAttribute("data-pane"))).toEqual(["T1", "T2"]);
    fireEvent.click(screen.getAllByTestId("ide-workspace-agent-row")[0]);
    expect(useIdeChatStore.getState().paneRequest).toMatchObject({ workspaceId: "w1", pane: "T1" });
    expect(screen.queryByTestId("ide-agents-column-done")).toBeNull();
  });

  it("shows no unrelated cards while loading, on no match, or on a failed search", () => {
    render(<AgentsOverview />);
    const worker = search();
    expect(screen.queryByTestId("ide-workspace-agent-row")).toBeNull();
    act(() => worker.reply({ type: "loading" }));
    expect(screen.getByRole("status").textContent).toContain("downloading");
    act(() => worker.reply({ type: "result", matches: [] }));
    expect(screen.getByRole("status").textContent).toBe("No matching terminals.");
    expect(screen.queryByTestId("ide-workspace-agent-row")).toBeNull();
    act(() => worker.reply({ type: "error" }));
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(screen.queryByTestId("ide-workspace-agent-row")).toBeNull();
  });

  it("restores the status columns with clear or Escape", () => {
    render(<AgentsOverview />);
    search();
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect(screen.getAllByTestId("ide-workspace-agent-row")).toHaveLength(3);
    search();
    fireEvent.keyDown(screen.getByRole("searchbox"), { key: "Escape" });
    expect(screen.getAllByTestId("ide-workspace-agent-row")).toHaveLength(3);
  });

  it("updates the search scope and keeps cross-workspace navigation", () => {
    render(<AgentsOverview />);
    const worker = search();
    fireEvent.click(screen.getByTestId("ide-agents-scope-folder"));
    act(() => vi.advanceTimersByTime(300));
    expect(worker.messages.at(-1)).toMatchObject({ documents: expect.arrayContaining([expect.objectContaining({ id: "T1@w2" })]) });
    act(() => worker.reply({ type: "result", matches: [{ id: "T1@w2", score: 0.9 }] }));
    fireEvent.click(screen.getByTestId("ide-workspace-agent-row"));
    expect(useIdeChatStore.getState().paneRequest).toMatchObject({ workspaceId: "w2", pane: "T1" });
  });
});
