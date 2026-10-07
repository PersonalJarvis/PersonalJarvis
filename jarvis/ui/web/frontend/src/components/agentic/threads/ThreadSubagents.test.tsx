import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SubagentState, ToolBlock, TurnBlock, TurnItem, TurnStatus } from "@/components/agentchat/reduce";
import { ThreadTimeline } from "./ThreadTimeline";
import { OpenSubagent, SubagentHeader, SubagentSwitcher } from "./ThreadSubagents";
import { listSubagents } from "./subagents";

const sub = (over: Partial<SubagentState> = {}): SubagentState => ({
  description: "Explore the auth code", agentType: "Explore", prompt: "Find where tokens are checked.", background: true,
  threadId: "", status: "running", summary: "", activity: "Reading auth.py", lastTool: "Read", tokens: 12400,
  toolUses: 3, durationMs: null, startedMs: Date.now(), finishedMs: null, blocks: [], ...over,
});

const spawn = (id: string, over: Partial<SubagentState> = {}): ToolBlock => ({
  kind: "tool", callId: id, name: "Agent", input: { description: "x" }, output: "Async agent launched successfully.",
  isError: false, durationMs: 10, approval: null, startedMs: 1, subagent: sub(over),
});

function turn(blocks: TurnBlock[], status: TurnStatus = "running"): TurnItem {
  return {
    type: "turn", id: "t1", provider: "claude-cli", model: "", effort: "", runner: "", status, blocks,
    startedMs: Date.now(), durationMs: status === "running" ? null : 5000, usage: null, liveUsage: null, costUsd: null, error: null,
  };
}

afterEach(cleanup);

describe("thread sub-agents", () => {
  it("shows a spawned sub-agent as a card that opens its conversation", () => {
    const open = vi.fn();
    render(<OpenSubagent.Provider value={open}>
      <ThreadTimeline items={[turn([spawn("a1")])]} sessionId="s1" bottomInset={0} />
    </OpenSubagent.Provider>);
    const card = screen.getByTestId("thread-subagent-card");
    expect(card.textContent).toContain("Explore the auth code");
    expect(card.textContent).toContain("Reading auth.py");
    expect(card.textContent).toContain("3 tools");
    expect(card.textContent).toContain("12.4k tokens");
    expect(screen.getByTestId("thread-working").textContent).toContain("1 sub-agent working");
    fireEvent.click(card);
    expect(open).toHaveBeenCalledWith("a1");
  });

  it("keeps a finished turn's sub-agents in view under its folded work", () => {
    render(<ThreadTimeline items={[turn([
      { kind: "tool", callId: "c0", name: "Bash", input: { command: "ls" }, output: "ok", isError: false, durationMs: 5, approval: null, startedMs: 1 },
      spawn("a1", { status: "done", summary: "Tokens are checked in auth.py." }),
      { kind: "text", id: "m1", text: "Done." },
    ], "done")]} sessionId="s1" bottomInset={0} />);
    expect(screen.getByTestId("thread-worked-for")).toBeTruthy();
    const card = screen.getByTestId("thread-subagent-card");
    expect(card.getAttribute("data-status")).toBe("done");
    expect(card.textContent).toContain("Tokens are checked in auth.py.");
  });

  it("counts the sub-agents and switches between them and the main thread", () => {
    const entries = listSubagents([turn([spawn("a1"), spawn("a2", { description: "Write tests", status: "done" })])]);
    const onOpen = vi.fn();
    render(<SubagentSwitcher entries={entries} openId="a2" onOpen={onOpen} />);
    expect(screen.getByTestId("thread-subagent-count").textContent).toBe("2 sub-agents · 1 working");
    const tabs = screen.getAllByTestId("thread-subagent-tab");
    expect(tabs.map((tab) => tab.getAttribute("aria-current"))).toEqual(["false", "true"]);
    fireEvent.click(tabs[0]);
    expect(onOpen).toHaveBeenCalledWith("a1");
    fireEvent.click(screen.getByTestId("thread-subagent-main"));
    expect(onOpen).toHaveBeenCalledWith(null);
  });

  it("opens with the task it was given and a way back", () => {
    const [entry] = listSubagents([turn([spawn("a1")])]);
    const onOpen = vi.fn();
    render(<SubagentHeader entry={entry} path={[entry]} onOpen={onOpen} />);
    expect(screen.getByRole("region", { name: "Task" }).textContent).toContain("Find where tokens are checked.");
    fireEvent.click(screen.getByTestId("thread-subagent-back"));
    expect(onOpen).toHaveBeenCalledWith(null);
  });

  it("says so when the CLI reports only the task and the answer", () => {
    const block: ToolBlock = { kind: "tool", callId: "c1", name: "task", input: { description: "Review", prompt: "Review it" }, output: "Fine.", isError: false, durationMs: 5, approval: null, startedMs: 1 };
    const [entry] = listSubagents([turn([block], "done")]);
    render(<SubagentHeader entry={entry} path={[entry]} onOpen={() => {}} />);
    expect(screen.getByTestId("thread-subagent-unstreamed")).toBeTruthy();
  });
});
