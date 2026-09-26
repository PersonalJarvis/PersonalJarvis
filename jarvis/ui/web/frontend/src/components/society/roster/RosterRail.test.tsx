import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SocietyAgent } from "@/components/society/data";
import { useRetireStore } from "@/components/society/world/retireStore";
import { useEventStore } from "@/store/events";
import { RosterRail } from "./RosterRail";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

afterEach(() => {
  useRetireStore.getState().cutShort();
  cleanup();
  localStorage.clear();
  useEventStore.setState({ assistantName: "Assistant" });
  vi.unstubAllGlobals();
});

function agent(over: Partial<SocietyAgent> & Pick<SocietyAgent, "agentId" | "name">): SocietyAgent {
  return {
    title: "Title",
    description: "",
    tier: "specialist",
    provider: "",
    providerLabel: "",
    model: "",
    effort: "",
    figure: null,
    palette: { primary: "#000", secondary: "#111", accent: "#222" },
    grantMode: "all",
    toolGrants: [],
    focus: [],
    denies: [],
    approvalRules: { requireApproval: [], alwaysAllow: [] },
    permissionCeiling: "ask",
    dailyBudgetUsd: 0,
    checkpoint: "idle",
    state: "idle",
    lifecycle: "active",
    createdMs: 0,
    maxConcurrentRuns: 1,
    workspaceDir: "",
    wikiNamespace: "",
    chatSessionId: null,
    routines: [],
    stats: { runs: 0, totalCostUsd: 0, spentTodayUsd: 0, lastActiveMs: null },
    ...over,
  };
}

const baseProps = {
  loading: false,
  sample: false,
  onOpen: () => undefined,
  onCreate: () => undefined,
};

describe("RosterRail lead name", () => {
  it("shows the configured name, updates it live, and finds it in search", () => {
    useEventStore.setState({ assistantName: "Nova" });
    const lead = agent({ agentId: "jarvis", name: "Jarvis", tier: "lead", title: "Lead" });
    render(<RosterRail {...baseProps} agents={[lead]} activeAgentId={null} />);

    expect(screen.getByText("Nova")).toBeTruthy();
    expect(screen.queryByText("Jarvis")).toBeNull();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "nova" } });
    expect(screen.getByText("Nova")).toBeTruthy();

    act(() => useEventStore.setState({ assistantName: "Hanna" }));
    expect(screen.queryByText("Nova")).toBeNull();
    expect(screen.queryByTestId("society-lead-hero")).toBeNull();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "hanna" } });
    expect(screen.getByText("Hanna")).toBeTruthy();
  });
});

describe("RosterRail status", () => {
  it("shows a loading spinner while an agent is thinking", () => {
    render(
      <RosterRail
        {...baseProps}
        agents={[agent({ agentId: "a", name: "A", state: "working" })]}
        activeAgentId={null}
      />,
    );
    expect(screen.getByRole("status", { name: "society.roster.thinking" })).toBeTruthy();
    expect(screen.queryByLabelText("society.roster.unread")).toBeNull();
  });

  it("shows a grey idle dot with no unseen results", () => {
    render(
      <RosterRail
        {...baseProps}
        agents={[agent({ agentId: "a", name: "A", state: "idle" })]}
        activeAgentId={null}
      />,
    );
    expect(screen.getByLabelText("society.state.idle")).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("marks a finished agent green until it is opened", () => {
    const working = [agent({ agentId: "a", name: "A", state: "working" })];
    const done = [agent({ agentId: "a", name: "A", state: "idle" })];
    const { rerender } = render(
      <RosterRail {...baseProps} agents={working} activeAgentId={null} />,
    );
    rerender(<RosterRail {...baseProps} agents={done} activeAgentId={null} />);
    expect(screen.getByLabelText("society.roster.unread")).toBeTruthy();

    rerender(<RosterRail {...baseProps} agents={done} activeAgentId="a" />);
    expect(screen.queryByLabelText("society.roster.unread")).toBeNull();
    expect(screen.getByLabelText("society.state.idle")).toBeTruthy();
  });

  it("does not mark the open agent unread when it finishes in front of you", () => {
    const working = [agent({ agentId: "a", name: "A", state: "working" })];
    const done = [agent({ agentId: "a", name: "A", state: "idle" })];
    const { rerender } = render(
      <RosterRail {...baseProps} agents={working} activeAgentId="a" />,
    );
    rerender(<RosterRail {...baseProps} agents={done} activeAgentId="a" />);
    expect(screen.queryByLabelText("society.roster.unread")).toBeNull();
  });
});

describe("RosterRail agent actions", () => {
  const renderRail = (agents: SocietyAgent[]) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}>
      <RosterRail {...baseProps} agents={agents} activeAgentId={null} />
    </QueryClientProvider>);
  };

  it("opens exactly the three requested actions on right click", () => {
    renderRail([agent({ agentId: "a", name: "A" })]);
    fireEvent.contextMenu(screen.getByText("A"));
    expect(screen.getAllByRole("menuitem")).toHaveLength(3);
    expect(screen.getByRole("menuitem", { name: "society.roster.rename" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "society.roster.hide" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "society.roster.delete" })).toBeTruthy();
    expect(screen.queryByText(/copy|kopieren/i)).toBeNull();
  });

  it("hides an agent persistently and restores it from the hidden list", () => {
    const agents = [agent({ agentId: "a", name: "A" })];
    const { unmount } = renderRail(agents);
    fireEvent.contextMenu(screen.getByText("A"));
    fireEvent.click(screen.getByRole("menuitem", { name: "society.roster.hide" }));
    expect(screen.queryByText("A")).toBeNull();
    unmount();

    renderRail(agents);
    expect(screen.queryByText("A")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "society.roster.show_hidden" }));
    fireEvent.contextMenu(screen.getByText("A"));
    fireEvent.click(screen.getByRole("menuitem", { name: "society.roster.show" }));
    expect(JSON.parse(localStorage.getItem("society.roster.hidden-agent-ids") ?? "[]")).toEqual([]);
    expect(screen.getByText("A")).toBeTruthy();
  });

  it("renames via the existing roster API and confirms before deleting", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
    renderRail([agent({ agentId: "a", name: "A" })]);
    fireEvent.contextMenu(screen.getByText("A"));
    fireEvent.click(screen.getByRole("menuitem", { name: "society.roster.rename" }));
    fireEvent.change(screen.getByRole("textbox", { name: "society.roster.name" }), { target: { value: "Renamed" } });
    fireEvent.click(screen.getByRole("button", { name: "society.roster.save" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/society/agents/a", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ name: "Renamed" }) })));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.contextMenu(screen.getByText("A"));
    fireEvent.click(screen.getByRole("menuitem", { name: "society.roster.delete" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "society.roster.cancel" }));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("archives only after the delete confirmation", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
    renderRail([agent({ agentId: "a", name: "A" })]);
    fireEvent.contextMenu(screen.getByText("A"));
    fireEvent.click(screen.getByRole("menuitem", { name: "society.roster.delete" }));
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "society.roster.delete" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/society/agents/a", { method: "DELETE" }));
  });
});
