import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ToolBlock, TurnBlock, TurnItem, TurnStatus } from "@/components/agentchat/reduce";
import { ThreadTurn } from "./ThreadTimeline";

const tool = (id: string, command: string, over: Partial<ToolBlock> = {}): ToolBlock => ({
  kind: "tool", callId: id, name: "Bash", input: { command }, output: "ok", isError: false,
  durationMs: 300, approval: null, startedMs: 1, ...over,
});
const thought = (id: string, text: string, live = false): TurnBlock => ({ kind: "reasoning", id, text, durationMs: live ? null : 4200, live, startedMs: 1 });

function turn(blocks: TurnBlock[], status: TurnStatus = "done"): TurnItem {
  return {
    type: "turn", id: "t1", provider: "claude-api", model: "", effort: "", runner: "", status, blocks,
    startedMs: Date.now(), durationMs: status === "running" ? null : 5000, usage: null, liveUsage: null, costUsd: null, error: null,
  };
}

afterEach(cleanup);

/** The thread turn as an agent chat draws it: questions and approvals answered in place. */
describe("a thread turn in an agent chat", () => {
  it("reads a streaming turn like a thread and folds it once it answered", () => {
    const blocks = [thought("r1", "**Checking the inbox**\n\nThree new mails."), tool("c1", "gh pr list"), tool("c2", "gh pr view 3", { output: null, durationMs: null })];
    const { rerender } = render(<ThreadTurn turn={turn(blocks, "running")} prompts="inline" />);
    // The worded thought is one short line; the running group stays open with its steps.
    expect(screen.getByTestId("thread-thought").textContent).toContain("Checking the inbox");
    expect(screen.queryByText("Three new mails.")).toBeNull();
    expect(screen.getByRole("button", { name: /steps so far/ }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("thread-working").textContent).toMatch(/^Working for /);
    const done = [...blocks.slice(0, 2), { ...blocks[2], output: "ok", durationMs: 200 } as ToolBlock, { kind: "text" as const, id: "a", text: "Two PRs wait for review." }];
    rerender(<ThreadTurn turn={turn(done)} prompts="inline" receipt="1.2k tokens" />);
    // Finished: only the answer and the "Worked for" line stay; one tap brings the work back.
    expect(screen.getByText("Two PRs wait for review.")).toBeTruthy();
    expect(screen.queryByTestId("thread-thought")).toBeNull();
    expect(screen.getByTestId("thread-worked-for").textContent).toContain("Worked for 5.0s");
    expect(screen.getByTestId("thread-turn-closing").textContent).toContain("1.2k tokens");
    fireEvent.click(screen.getByTestId("thread-worked-for"));
    expect(screen.getByTestId("thread-thought").textContent).toContain("Checking the inbox");
  });

  it("never draws a thought the model did not put into words", () => {
    render(<ThreadTurn turn={turn([thought("r1", ""), tool("c1", "ls"), { kind: "text", id: "a", text: "Done." }])} prompts="inline" />);
    fireEvent.click(screen.getByTestId("thread-worked-for"));
    expect(screen.queryByTestId("thread-thought")).toBeNull();
    expect(screen.queryByText("Thinking")).toBeNull();
  });

  it("answers an approval in place and keeps it out of the fold", async () => {
    const onDecide = vi.fn();
    const asking = tool("c2", "git push", { output: null, durationMs: null, approval: { approvalId: "ap1", summary: "Push to origin?", decision: null } });
    render(<ThreadTurn turn={turn([tool("c1", "git status"), asking, thought("r1", "Waiting on the push.")])} prompts="inline" onDecide={onDecide} />);
    // The turn ended while it waited: the work folds, the approval stays in view.
    expect(screen.getByTestId("thread-worked-for")).toBeTruthy();
    expect(screen.getByText("Push to origin?")).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Approve" })); });
    expect(onDecide).toHaveBeenCalledWith("ap1", "allow");
  });

  it("says a thread waits for its composer when prompts are answered there", () => {
    const asking = tool("c1", "git push", { output: null, durationMs: null, approval: { approvalId: "ap1", summary: "Push to origin?", decision: null } });
    render(<ThreadTurn turn={turn([asking], "running")} />);
    expect(screen.getByText("Waiting for approval — Push to origin?")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("folds memory receipts with the work, above the answer", () => {
    const extras = [{ key: "m1", node: <span>MEMORY-STEP</span> }];
    render(<ThreadTurn turn={turn([{ kind: "text", id: "a", text: "Noted." }])} prompts="inline" extras={extras} />);
    expect(screen.queryByText("MEMORY-STEP")).toBeNull();
    fireEvent.click(screen.getByTestId("thread-worked-for"));
    const html = document.body.innerHTML;
    expect(html.indexOf("MEMORY-STEP")).toBeGreaterThan(-1);
    expect(html.indexOf("MEMORY-STEP")).toBeLessThan(html.indexOf("Noted."));
  });

  it("reports a sub-agent without offering to open it where no conversation opens", () => {
    const task = tool("c1", "", { name: "Task", input: { description: "Scan the repo", prompt: "Scan" }, output: null, durationMs: null });
    render(<ThreadTurn turn={turn([task], "running")} prompts="inline" />);
    const card = screen.getByTestId("thread-subagent-card");
    expect(card.tagName).toBe("DIV");
    expect(screen.queryByRole("button", { name: /Open sub-agent/ })).toBeNull();
  });
});
