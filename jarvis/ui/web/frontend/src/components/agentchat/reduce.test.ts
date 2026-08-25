import { describe, expect, it } from "vitest";

import type { AgentChatEvent } from "@/lib/agentChatApi";
import { EMPTY_TIMELINE, reduceEvent, reduceEvents, runningTurn, type TurnItem } from "./reduce";

let seq = 0;
function ev(kind: string, payload: Record<string, unknown>, persisted = true): AgentChatEvent {
  if (persisted) seq += 1;
  return { seq: persisted ? seq : 0, ts_ms: 1_000 + seq, kind, payload };
}

describe("agent-chat reduce", () => {
  it("folds a full turn: user line, deltas into one text block, tool call + result, finish", () => {
    const tl = reduceEvents(EMPTY_TIMELINE, [
      ev("user_message", { text: "hi" }),
      ev("turn_started", { turn_id: "t1", provider: "claude-api", model: "opus", effort: "high", runner: "claude-cli" }),
      ev("text_delta", { turn_id: "t1", message_id: "m1", text: "Hel" }, false),
      ev("text_delta", { turn_id: "t1", message_id: "m1", text: "lo" }, false),
      ev("tool_call", { turn_id: "t1", call_id: "c1", name: "Read", input: { file_path: "a.py" } }),
      ev("tool_result", { turn_id: "t1", call_id: "c1", output: "print(1)", is_error: false, duration_ms: 12 }),
      ev("assistant_text", { turn_id: "t1", message_id: "m1", text: "Hello" }),
      ev("turn_finished", { turn_id: "t1", status: "done", duration_ms: 900, usage: { input_tokens: 3 } }),
    ]);
    expect(tl.items).toHaveLength(2);
    expect(tl.items[0]).toMatchObject({ type: "user", text: "hi" });
    const turn = tl.items[1] as TurnItem;
    expect(turn.status).toBe("done");
    expect(turn.blocks.map((b) => b.kind)).toEqual(["text", "tool"]);
    expect(turn.blocks[0]).toMatchObject({ kind: "text", text: "Hello" });
    expect(turn.blocks[1]).toMatchObject({ kind: "tool", name: "Read", output: "print(1)", durationMs: 12 });
    expect(turn.durationMs).toBe(900);
    expect(tl.lastSeq).toBeGreaterThan(0);
    expect(runningTurn(tl)).toBeNull();
  });

  it("tracks approvals: pending until resolved, and the tool row carries the decision", () => {
    let tl = reduceEvents(EMPTY_TIMELINE, [
      ev("turn_started", { turn_id: "t2", provider: "openai", model: "", effort: "", runner: "api" }),
      ev("approval_required", { turn_id: "t2", approval_id: "a1", call_id: "c2", name: "RunCommand", input: { command: "rm x" }, summary: "rm x" }),
    ]);
    expect(tl.pendingApprovals).toHaveLength(1);
    expect(runningTurn(tl)?.id).toBe("t2");
    tl = reduceEvent(tl, ev("approval_resolved", { turn_id: "t2", approval_id: "a1", decision: "deny" }));
    expect(tl.pendingApprovals).toHaveLength(0);
    const turn = tl.items[0] as TurnItem;
    expect(turn.blocks[0]).toMatchObject({ kind: "tool", approval: { approvalId: "a1", decision: "deny" } });
  });

  it("returns the same object when an event changes nothing", () => {
    const tl = reduceEvents(EMPTY_TIMELINE, [ev("turn_started", { turn_id: "t3" })]);
    const same = reduceEvent(tl, ev("text_delta", { turn_id: "t3", message_id: "m", text: "" }, false));
    expect(same).toBe(tl);
    const unknownTurn = reduceEvent(tl, ev("tool_call", { turn_id: "nope", call_id: "c" }));
    expect(unknownTurn.items).toEqual(tl.items);
  });

  it("closes live reasoning on the finished block and exposes session patches once", () => {
    let tl = reduceEvents(EMPTY_TIMELINE, [
      ev("turn_started", { turn_id: "t4" }),
      ev("reasoning_delta", { turn_id: "t4", text: "hm" }, false),
      ev("reasoning_delta", { turn_id: "t4", text: "m" }, false),
    ]);
    expect((tl.items[0] as TurnItem).blocks[0]).toMatchObject({ kind: "reasoning", text: "hmm", live: true });
    tl = reduceEvent(tl, ev("reasoning", { turn_id: "t4", text: "hmm!", duration_ms: 400 }));
    expect((tl.items[0] as TurnItem).blocks[0]).toMatchObject({ kind: "reasoning", text: "hmm!", live: false, durationMs: 400 });
    tl = reduceEvent(tl, ev("session_updated", { permission_mode: "auto" }));
    expect(tl.sessionPatch).toEqual({ permission_mode: "auto" });
    tl = reduceEvent(tl, ev("turn_finished", { turn_id: "t4", status: "cancelled" }));
    expect(tl.sessionPatch).toBeNull();
    expect((tl.items[0] as TurnItem).status).toBe("cancelled");
  });

  it("shows redacted thinking: announced live, closed by the textless finished block", () => {
    let tl = reduceEvents(EMPTY_TIMELINE, [
      ev("turn_started", { turn_id: "t5" }),
      ev("reasoning_started", { turn_id: "t5", message_id: "m1" }, false),
    ]);
    let turn = tl.items[0] as TurnItem;
    expect(turn.blocks[0]).toMatchObject({ kind: "reasoning", text: "", live: true });
    // A second announcement for the same thought does not add a row.
    tl = reduceEvent(tl, ev("reasoning_started", { turn_id: "t5", message_id: "m1" }, false));
    expect((tl.items[0] as TurnItem).blocks).toHaveLength(1);
    tl = reduceEvent(tl, ev("reasoning", { turn_id: "t5", text: "", duration_ms: 8500 }));
    turn = tl.items[0] as TurnItem;
    expect(turn.blocks[0]).toMatchObject({ kind: "reasoning", text: "", live: false, durationMs: 8500 });
    // A textless finished block with a duration and no live row still lands.
    tl = reduceEvent(tl, ev("reasoning", { turn_id: "t5", text: "", duration_ms: 1200 }));
    expect((tl.items[0] as TurnItem).blocks).toHaveLength(2);
    // ...but one with neither text nor time is nothing.
    tl = reduceEvent(tl, ev("reasoning", { turn_id: "t5", text: "", duration_ms: 0 }));
    expect((tl.items[0] as TurnItem).blocks).toHaveLength(2);
  });

  it("ends a live thought when text or a tool call follows, and times tool calls from the log", () => {
    let tl = reduceEvents(EMPTY_TIMELINE, [
      ev("turn_started", { turn_id: "t6" }),
      ev("reasoning_started", { turn_id: "t6", message_id: "m1" }, false),
      ev("tool_call", { turn_id: "t6", call_id: "c1", name: "Grep", input: { pattern: "x" } }),
    ]);
    let turn = tl.items[0] as TurnItem;
    expect(turn.blocks[0]).toMatchObject({ kind: "reasoning", live: false });
    expect(turn.blocks[1]).toMatchObject({ kind: "tool", name: "Grep" });
    const callTs = (turn.blocks[1] as { startedMs: number }).startedMs;
    const result = ev("tool_result", { turn_id: "t6", call_id: "c1", output: "hit", is_error: false });
    result.ts_ms = callTs + 340;
    tl = reduceEvent(tl, result);
    turn = tl.items[0] as TurnItem;
    expect(turn.blocks[1]).toMatchObject({ kind: "tool", output: "hit", durationMs: 340 });
  });

  it("carries a turn run by the brain — the front page's Jarvis — untouched", () => {
    // The typed front page is Jarvis on a keyboard: its turns are announced
    // with runner "brain" instead of a CLI's or the API runner's name. The
    // reducer keeps the word as given; nothing here decides what it means.
    const tl = reduceEvents(EMPTY_TIMELINE, [
      ev("user_message", { text: "what did I plan for today?" }),
      ev("turn_started", { turn_id: "t8", provider: "jarvis", model: "", effort: "", runner: "brain" }),
    ]);
    const turn = tl.items[1] as TurnItem;
    expect(turn.runner).toBe("brain");
    expect(turn.provider).toBe("jarvis");
    expect(runningTurn(tl)?.id).toBe("t8");
  });

  it("counts tokens live and keeps them when the finished turn brings no usage", () => {
    let tl = reduceEvents(EMPTY_TIMELINE, [
      ev("turn_started", { turn_id: "t7" }),
      ev("usage_delta", { turn_id: "t7", usage: { input_tokens: 3, output_tokens: 40 } }, false),
      ev("usage_delta", { turn_id: "t7", usage: { input_tokens: 4, output_tokens: 65 } }, false),
    ]);
    expect((tl.items[0] as TurnItem).liveUsage).toEqual({ input_tokens: 4, output_tokens: 65 });
    tl = reduceEvent(tl, ev("turn_finished", { turn_id: "t7", status: "done" }));
    const turn = tl.items[0] as TurnItem;
    expect(turn.usage).toEqual({ input_tokens: 4, output_tokens: 65 });
    expect(turn.durationMs).not.toBeNull();
  });
});
