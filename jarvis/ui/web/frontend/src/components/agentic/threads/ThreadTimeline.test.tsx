import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ToolBlock, TurnBlock, TurnItem, TurnStatus } from "@/components/agentchat/reduce";
import { projectPath, ThreadTimeline } from "./ThreadTimeline";

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

describe("changed files card", () => {
  const edit = (id: string, path: string): ToolBlock =>
    tool(id, "", { name: "Edit", input: { file_path: path, old_string: "a", new_string: "b\nc" } });

  it("lists three files and folds the rest behind a count", () => {
    const paths = ["src/a.ts", "src/b.ts", "docs/c.md", "lib/d.py", "e.json"];
    show(turn(paths.map((path, i) => edit(`e${i}`, path))));
    const card = screen.getByTestId("thread-changed-files");
    expect(card.textContent).toContain("5 files changed");
    expect(screen.getByText("a.ts")).toBeTruthy();
    expect(screen.getAllByText("src/").length).toBe(2);
    expect(screen.queryByText("d.py")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Show 2 more files/ }));
    expect(screen.getByText("d.py")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Collapse files/ }));
    expect(screen.queryByText("e.json")).toBeNull();
  });

  it("shows no fold line for three files or fewer", () => {
    show(turn([edit("e1", "a.ts"), edit("e2", "b.ts")]));
    expect(screen.queryByRole("button", { name: /more file/ })).toBeNull();
  });

  it("opens every diff at once from the header", () => {
    show(turn(["a.ts", "b.ts", "c.ts", "d.ts"].map((path, i) => edit(`e${i}`, path))));
    fireEvent.click(screen.getByRole("button", { name: "Show changes" }));
    expect(screen.getByText("d.ts")).toBeTruthy();
    expect(screen.getAllByRole("button", { expanded: true }).length).toBeGreaterThanOrEqual(4);
  });
});

describe("projectPath", () => {
  it("reads a file relative to the thread folder on every OS", () => {
    expect(projectPath("C:\\Repo\\src\\a.ts", "c:\\repo")).toBe("src/a.ts");
    expect(projectPath("/home/me/repo/src/a.ts", "/home/me/repo/")).toBe("src/a.ts");
    expect(projectPath("src/a.ts", "/home/me/repo")).toBe("src/a.ts");
    expect(projectPath("/etc/x/y/z/a.conf", "/home/me/repo")).toBe("…/y/z/a.conf");
  });
});
