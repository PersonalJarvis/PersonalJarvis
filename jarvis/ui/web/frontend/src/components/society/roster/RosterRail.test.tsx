import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SocietyAgent } from "@/components/society/data";
import { RosterRail } from "./RosterRail";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

afterEach(() => {
  cleanup();
  localStorage.clear();
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
