import { describe, expect, it } from "vitest";
import { EMPTY_TIMELINE, reduceEvents, type TextBlock, type ToolBlock, type TurnItem } from "@/components/agentchat/reduce";
import type { AgentChatEvent } from "@/lib/agentChatApi";
import { isSpawnTool, listSubagents, mergeDiscovered, subagentCounts, subagentPath, subagentTurn, type DiscoveredAgent } from "./subagents";

let seq = 0;
const ev = (kind: string, payload: Record<string, unknown>, ts = 1000 + seq): AgentChatEvent =>
  ({ seq: ++seq, ts_ms: ts, kind, payload } as AgentChatEvent);

/** The events the backend sends for one Claude Code turn that ran a background agent. */
function claudeRun(): AgentChatEvent[] {
  return [
    ev("turn_started", { turn_id: "t1", provider: "claude-cli" }),
    ev("tool_call", { turn_id: "t1", call_id: "spawn", name: "Agent", input: { description: "Count files", prompt: "Run echo" } }),
    ev("subagent_started", { turn_id: "t1", agent_id: "spawn", description: "Count files", agent_type: "general-purpose", prompt: "Run echo", background: true }),
    ev("tool_result", { turn_id: "t1", call_id: "spawn", output: "Async agent launched successfully.", is_error: false }),
    ev("tool_call", { turn_id: "t1", agent_id: "spawn", call_id: "bash", name: "Bash", input: { command: "echo hi" } }),
    ev("subagent_progress", { turn_id: "t1", agent_id: "spawn", activity: "Running echo", last_tool: "Bash", tokens: 23416, tool_uses: 1 }),
    ev("tool_result", { turn_id: "t1", agent_id: "spawn", call_id: "bash", output: "hi", is_error: false }),
    ev("assistant_text", { turn_id: "t1", agent_id: "spawn", message_id: "sub2", text: "hi" }),
  ];
}

const finished = (status = "completed") => [
  ev("subagent_finished", { turn_id: "t1", agent_id: "spawn", status: status === "completed" ? "done" : status, summary: "hi", tokens: 25617, tool_uses: 1, duration_ms: 5509 }),
  ev("assistant_text", { turn_id: "t1", message_id: "m2", text: "The agent said hi." }),
  ev("turn_finished", { turn_id: "t1", status: "done", duration_ms: 9000 }),
];

const turnOf = (events: AgentChatEvent[]) => reduceEvents(EMPTY_TIMELINE, events).items.find((item) => item.type === "turn") as TurnItem;

describe("sub-agents in the timeline", () => {
  it("files a sub-agent's steps and words under its spawn call, never the main answer", () => {
    const turn = turnOf([...claudeRun(), ...finished()]);
    const texts = turn.blocks.filter((b): b is TextBlock => b.kind === "text").map((b) => b.text);
    expect(texts).toEqual(["The agent said hi."]);
    const spawn = turn.blocks.find((b) => b.kind === "tool" && b.callId === "spawn") as ToolBlock;
    expect(spawn.subagent?.status).toBe("done");
    expect(spawn.subagent?.summary).toBe("hi");
    expect(spawn.subagent?.tokens).toBe(25617);
    expect(spawn.subagent?.blocks.map((b) => b.kind)).toEqual(["tool", "text"]);
    expect(turn.blocks.some((b) => b.kind === "tool" && b.callId === "bash")).toBe(false);
  });

  it("keeps a background agent working past its launch receipt, with what it does now", () => {
    const [entry] = listSubagents(reduceEvents(EMPTY_TIMELINE, claudeRun()).items);
    expect(entry.status).toBe("running");
    expect(entry.activity).toBe("Running echo");
    expect(entry.tokens).toBe(23416);
    expect(entry.title).toBe("Count files");
    expect(entry.agentType).toBe("general-purpose");
    expect(entry.streamed).toBe(true);
  });

  it("stops a background agent that never reported its end once the turn is over", () => {
    const turn = turnOf([...claudeRun(), ev("turn_finished", { turn_id: "t1", status: "cancelled" })]);
    const [entry] = listSubagents([turn]);
    expect(entry.status).toBe("stopped");
  });

  it("finishes a foreground agent by its spawn call's result", () => {
    const turn = turnOf([
      ev("turn_started", { turn_id: "t1" }),
      ev("tool_call", { turn_id: "t1", call_id: "spawn", name: "Agent", input: { description: "Look", prompt: "Look around" } }),
      ev("subagent_started", { turn_id: "t1", agent_id: "spawn", description: "Look", prompt: "Look around", background: false }),
      ev("tool_result", { turn_id: "t1", call_id: "spawn", output: "Found three files.", is_error: false }),
    ]);
    const [entry] = listSubagents([turn]);
    expect(entry.status).toBe("done");
    expect(entry.summary).toBe("Found three files.");
  });

  it("nests a sub-agent's own sub-agent and finds the way back to it", () => {
    const items = reduceEvents(EMPTY_TIMELINE, [
      ...claudeRun(),
      ev("tool_call", { turn_id: "t1", agent_id: "spawn", call_id: "inner", name: "Agent", input: { description: "Dig deeper" } }),
      ev("subagent_started", { turn_id: "t1", agent_id: "inner", description: "Dig deeper", prompt: "Dig", background: false }),
      ev("assistant_text", { turn_id: "t1", agent_id: "inner", message_id: "i1", text: "Dug." }),
    ]).items;
    const entries = listSubagents(items);
    expect(entries.map((entry) => [entry.id, entry.depth, entry.parentId])).toEqual([["spawn", 0, null], ["inner", 1, "spawn"]]);
    expect(subagentPath(entries, "inner").map((entry) => entry.id)).toEqual(["spawn", "inner"]);
    expect(entries[1].blocks).toHaveLength(1);
    expect(subagentCounts(entries)).toEqual({ total: 2, running: 2, failed: 0, waiting: 0 });
  });

  it("puts an approval a sub-agent asks for on its own call and marks the agent waiting", () => {
    const items = reduceEvents(EMPTY_TIMELINE, [
      ...claudeRun(),
      ev("tool_call", { turn_id: "t1", agent_id: "spawn", call_id: "rm", name: "Bash", input: { command: "rm -rf build" } }),
      ev("approval_required", { turn_id: "t1", approval_id: "a1", call_id: "rm", name: "Bash", input: {}, summary: "rm -rf build" }),
    ]).items;
    const [entry] = listSubagents(items);
    expect(entry.waiting).toBe(true);
    const turn = items.find((item) => item.type === "turn") as TurnItem;
    expect(turn.blocks.some((b) => b.kind === "tool" && b.callId === "rm")).toBe(false);
    const resolved = listSubagents(reduceEvents({ ...EMPTY_TIMELINE, items }, [ev("approval_resolved", { turn_id: "t1", approval_id: "a1", decision: "allow" })]).items);
    expect(resolved[0].waiting).toBe(false);
  });

  it("reads any CLI's spawn call as a sub-agent with its task and answer", () => {
    expect(isSpawnTool("task")).toBe(true);
    expect(isSpawnTool("Task")).toBe(true);
    expect(isSpawnTool("spawn_agent")).toBe(true);
    expect(isSpawnTool("TaskCreate")).toBe(false);
    expect(isSpawnTool("Bash")).toBe(false);
    const turn = turnOf([
      ev("turn_started", { turn_id: "t1", provider: "opencode-cli" }),
      ev("tool_call", { turn_id: "t1", call_id: "c1", name: "task", input: { description: "Review", prompt: "Review the diff", subagent_type: "reviewer" } }),
      ev("tool_result", { turn_id: "t1", call_id: "c1", output: "Two issues.", is_error: false }),
    ]);
    const [entry] = listSubagents([turn]);
    expect(entry).toMatchObject({ title: "Review", agentType: "reviewer", prompt: "Review the diff", status: "done", summary: "Two issues.", streamed: false });
    // Its conversation is its answer.
    expect(subagentTurn(entry).blocks).toEqual([{ kind: "text", id: "answer:c1", text: "Two issues." }]);
  });

  it("reads a Codex agent's answer from its finished report", () => {
    const turn = turnOf([
      ev("turn_started", { turn_id: "t1", provider: "codex-cli" }),
      ev("tool_call", { turn_id: "t1", call_id: "item_3", name: "spawn_agent", input: { prompt: "Reply with PONG" } }),
      ev("tool_result", { turn_id: "t1", call_id: "item_3", output: "child-1", is_error: false }),
      ev("subagent_started", { turn_id: "t1", agent_id: "item_3", thread_id: "child-1", description: "Reply with PONG", prompt: "Reply with PONG", background: true }),
    ]);
    expect(listSubagents([turn])[0].status).toBe("running");
    const done = turnOf([
      ...[
        ev("turn_started", { turn_id: "t1", provider: "codex-cli" }),
        ev("tool_call", { turn_id: "t1", call_id: "item_3", name: "spawn_agent", input: { prompt: "Reply with PONG" } }),
        ev("subagent_started", { turn_id: "t1", agent_id: "item_3", thread_id: "child-1", prompt: "Reply with PONG", background: true }),
        ev("tool_result", { turn_id: "t1", call_id: "item_3", output: "child-1", is_error: false }),
      ],
      ev("subagent_finished", { turn_id: "t1", agent_id: "item_3", status: "done", summary: "PONG" }),
    ]);
    const [entry] = listSubagents([done]);
    expect(entry.status).toBe("done");
    expect(subagentTurn(entry).blocks).toEqual([{ kind: "text", id: "answer:item_3", text: "PONG" }]);
  });
});

describe("sub-agents the CLI filed on its own", () => {
  const discovered = (over: Partial<DiscoveredAgent> = {}): DiscoveredAgent => ({
    thread_id: "child-1", parent_thread_id: "root", nickname: "Heisenberg", role: "default", path: "/root/grok_research",
    started_ms: 2000, updated_ms: 5000, status: "done", summary: "Found it.",
    events: [
      { seq: 0, ts_ms: 2100, kind: "tool_call", payload: { call_id: "c1", name: "RunCommand", input: { command: "ls" } } } as AgentChatEvent,
      { seq: 0, ts_ms: 2200, kind: "tool_result", payload: { call_id: "c1", output: "a.txt", is_error: false } } as AgentChatEvent,
      { seq: 0, ts_ms: 2300, kind: "assistant_text", payload: { message_id: "m1", text: "Found it." } } as AgentChatEvent,
    ],
    ...over,
  });
  const codexTurn = (status: "running" | "done"): TurnItem => turnOf([
    ev("turn_started", { turn_id: "t1", provider: "openai-codex" }, 1000),
    ev("tool_call", { turn_id: "t1", call_id: "c0", name: "RunCommand", input: { command: "git status" } }, 1500),
    ev("assistant_text", { turn_id: "t1", message_id: "m0", text: "All done." }, 6000),
    ...(status === "done" ? [ev("turn_finished", { turn_id: "t1", status: "done" }, 7000)] : []),
  ]);

  it("gives an unannounced Codex agent a card with its own steps in the turn it ran in", () => {
    const merged = mergeDiscovered([codexTurn("done")], [discovered()], "root");
    const [entry] = listSubagents(merged);
    expect(entry).toMatchObject({ title: "grok research", agentType: "Heisenberg", status: "done", summary: "Found it.", streamed: true });
    expect(entry.blocks.map((b) => b.kind)).toEqual(["tool", "text"]);
    // The card sits before the closing answer.
    const turn = merged[0] as TurnItem;
    expect(turn.blocks.map((b) => (b.kind === "tool" ? b.callId : b.kind))).toEqual(["c0", "codex:child-1", "text"]);
  });

  it("fills a spawn the stream announced instead of adding a second card", () => {
    const turn = turnOf([
      ev("turn_started", { turn_id: "t1", provider: "openai-codex" }, 1000),
      ev("tool_call", { turn_id: "t1", call_id: "item_3", name: "spawn_agent", input: { prompt: "Research" } }, 1500),
      ev("subagent_started", { turn_id: "t1", agent_id: "item_3", thread_id: "child-1", prompt: "Research", background: true }, 1500),
    ]);
    const entries = listSubagents(mergeDiscovered([turn], [discovered({ status: "running", summary: "" })], "root"));
    expect(entries).toHaveLength(1);
    expect(entries[0].id).toBe("item_3");
    expect(entries[0].streamed).toBe(true);
    expect(entries[0].status).toBe("running");
  });

  it("nests a sub-agent's own sub-agent and stops what a finished turn left running", () => {
    const merged = mergeDiscovered([codexTurn("done")], [
      discovered(),
      discovered({ thread_id: "grand-1", parent_thread_id: "child-1", path: "/root/grok_research/tests", status: "running", events: [] }),
    ], "root");
    const entries = listSubagents(merged);
    expect(entries.map((entry) => [entry.id, entry.depth, entry.parentId])).toEqual([["codex:child-1", 0, null], ["codex:grand-1", 1, "codex:child-1"]]);
    const lone = listSubagents(mergeDiscovered([codexTurn("done")], [discovered({ status: "running" })], "root"));
    expect(lone[0].status).toBe("stopped");
  });
});
