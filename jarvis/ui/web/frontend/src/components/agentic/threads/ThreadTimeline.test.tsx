import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ToolBlock, TurnBlock, TurnItem, TurnStatus } from "@/components/agentchat/reduce";
import { ThreadTimeline } from "./ThreadTimeline";

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

const show = (item: TurnItem) => render(<ThreadTimeline items={[item]} sessionId="s1" bottomInset={0} />);

afterEach(cleanup);

describe("thread work log", () => {
  it("folds a finished stretch to one counted line that opens to its steps", () => {
    show(turn([thought("r1", "**Checking the build**"), tool("c1", "npm run build"), tool("c2", "npm test"), { kind: "text", id: "n1", text: "All green." }]));
    const header = screen.getByRole("button", { name: /Ran 2 commands/ });
    expect(screen.queryByText("npm test")).toBeNull();
    fireEvent.click(header);
    expect(screen.getByText("npm test")).toBeTruthy();
    // The thought reads as its own words, without the Markdown marks.
    expect(screen.getByText("Checking the build")).toBeTruthy();
    expect(screen.getByText("All green.")).toBeTruthy();
  });

  it("opens a thought to its full text under a timed heading", () => {
    show(turn([thought("r1", "First idea.\n\nSecond idea.")]));
    fireEvent.click(screen.getByRole("button", { name: /First idea\. Second idea\./ }));
    expect(screen.getByText("Thought for 4.2s")).toBeTruthy();
    expect(screen.getByText("Second idea.")).toBeTruthy();
  });

  it("names the newest step of the running stretch and keeps the turn clock", () => {
    show(turn([tool("c1", "npm run build"), tool("c2", "npm test", { output: null, durationMs: null })], "running"));
    expect(screen.getByText("npm test")).toBeTruthy();
    expect(screen.queryByText("npm run build")).toBeNull();
    expect(screen.getByTestId("thread-working").textContent).toMatch(/^Working for /);
  });

  it("follows a live thought by its newest paragraph", () => {
    show(turn([thought("r1", "Reading the config.\n\nNow the router.", true)], "running"));
    expect(screen.getByText("Now the router.")).toBeTruthy();
  });
});
