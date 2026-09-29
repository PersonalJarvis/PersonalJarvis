import { describe, expect, it } from "vitest";
import type { TimelineItem, TurnItem } from "@/components/agentchat/reduce";
import { EMPTY_TIMELINE } from "@/components/agentchat/reduce";
import { ambientBubble, bubbleText, conversationBubble, itemsFor, type BubbleLabels } from "./officeTalk";

const LABELS: BubbleLabels = {
  thinking: "Thinking", tool: "Using {0}", failed: "Failed", cancelled: "Stopped", done: "Done", approval: "Need OK", waiting: "Waiting",
};

function turn(patch: Partial<TurnItem>): TurnItem {
  return {
    type: "turn", id: "t1", provider: "p", model: "m", effort: "", runner: "api", status: "done", blocks: [],
    startedMs: 1000, durationMs: 500, usage: null, liveUsage: null, costUsd: null, error: null, ...patch,
  };
}

const user: TimelineItem = { type: "user", id: "u1", text: "Hi", attachments: [], tsMs: 900 };

describe("bubbleText", () => {
  it("strips markdown and flattens whitespace", () => {
    expect(bubbleText("## Plan\n- **Fix** the `login` [bug](http://x)\n\n```js\ncode()\n```")).toBe("Plan Fix the login bug");
  });

  it("cuts long text at a word boundary with an ellipsis", () => {
    const out = bubbleText("word ".repeat(80), 40);
    expect(out.length).toBeLessThanOrEqual(40);
    expect(out.endsWith("…")).toBe(true);
    expect(out).not.toMatch(/\s…$/);
  });
});

describe("conversationBubble", () => {
  it("is empty before anything was said", () => {
    expect(conversationBubble([], 0, LABELS)).toBeNull();
  });

  it("thinks while the sent message waits for its turn", () => {
    expect(conversationBubble([user], 0, LABELS)).toMatchObject({ kind: "thought", text: "Thinking", live: true });
  });

  it("names the running tool in a thought bubble", () => {
    const running = turn({ status: "running", blocks: [{ kind: "tool", callId: "c", name: "mcp__jarvis__web_search", input: {}, output: null, isError: false, durationMs: null, approval: null, startedMs: 1 }] });
    expect(conversationBubble([user, running], 0, LABELS)).toMatchObject({ kind: "thought", text: "Using web_search", live: true });
  });

  it("speaks the streaming text live, then the finished reply", () => {
    const streaming = turn({ status: "running", blocks: [{ kind: "text", id: "x", text: "Working on **it**" }] });
    expect(conversationBubble([user, streaming], 0, LABELS)).toMatchObject({ kind: "speech", text: "Working on it", live: true });
    const done = turn({ blocks: [{ kind: "text", id: "x", text: "All done." }] });
    expect(conversationBubble([user, done], 0, LABELS)).toEqual({ kind: "speech", text: "All done.", live: false, atMs: 1500 });
  });

  it("reports a failed turn and asks when an approval is pending", () => {
    expect(conversationBubble([turn({ status: "error", error: "Rate limited" })], 0, LABELS)).toMatchObject({ kind: "error", text: "Rate limited" });
    expect(conversationBubble([user], 1, LABELS)).toMatchObject({ kind: "ask", text: "Need OK" });
  });
});

describe("itemsFor", () => {
  const timeline = { ...EMPTY_TIMELINE, items: [user] };
  it("reads a specialist's items only from its own open session", () => {
    expect(itemsFor({ tier: "specialist", chatSessionId: "s1" }, "s1", timeline)).toEqual([user]);
    expect(itemsFor({ tier: "specialist", chatSessionId: "s1" }, "s2", timeline)).toEqual([]);
    expect(itemsFor({ tier: "specialist", chatSessionId: null }, null, timeline)).toEqual([]);
  });

  it("reads the lead from the Jarvis chat", () => {
    expect(itemsFor({ tier: "lead", chatSessionId: null }, "any", timeline)).toEqual([user]);
  });
});

describe("ambientBubble", () => {
  it("lets working agents think about their current step", () => {
    expect(ambientBubble("working", [{ kind: "tool", text: "read_file" }], LABELS)).toMatchObject({ kind: "thought", text: "Using read_file" });
    expect(ambientBubble("working", undefined, LABELS)).toMatchObject({ kind: "thought", text: "Thinking" });
  });

  it("lets waiting agents ask, and keeps idle agents quiet", () => {
    expect(ambientBubble("waiting", [{ kind: "agent", text: "Which repo?" }, { kind: "tool", text: "x" }], LABELS)).toMatchObject({ kind: "ask", text: "Which repo?" });
    expect(ambientBubble("waiting", [], LABELS)).toMatchObject({ kind: "ask", text: "Waiting" });
    expect(ambientBubble("idle", [{ kind: "agent", text: "hi" }], LABELS)).toBeNull();
  });
});
