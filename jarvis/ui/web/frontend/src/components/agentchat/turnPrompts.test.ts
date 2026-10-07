import { describe, expect, it } from "vitest";

import type { AgentChatEvent } from "@/lib/agentChatApi";
import { ASK_FENCE, hideAskBlocks } from "./askFence";
import { EMPTY_TIMELINE, reduceEvents, type TextBlock, type ToolBlock, type TurnItem } from "./reduce";

let seq = 0;
function ev(kind: string, payload: Record<string, unknown>, persisted = true): AgentChatEvent {
  if (persisted) seq += 1;
  return { seq: persisted ? seq : 0, ts_ms: 1_000 + seq, kind, payload };
}

const QUESTIONS = [{ question: "Which database?", options: [{ label: "SQLite" }, { label: "Postgres" }] }];
const BLOCK = `\`\`\`${ASK_FENCE}\n${JSON.stringify({ questions: QUESTIONS })}\n\`\`\``;

function turn(tl: { items: unknown[] }, index = 1): TurnItem {
  return tl.items[index] as TurnItem;
}

describe("end-of-turn cards", () => {
  it("hides the question block from the reply, also while it streams in", () => {
    expect(hideAskBlocks(`Two choices.\n\n${BLOCK}\n`)).toBe("Two choices.");
    expect(hideAskBlocks(`Two choices.\n\n\`\`\`${ASK_FENCE}\n{"questions": [`)).toBe("Two choices.");
    expect(hideAskBlocks("```json\n{}\n```")).toBe("```json\n{}\n```");

    const tl = reduceEvents(EMPTY_TIMELINE, [
      ev("turn_started", { turn_id: "t1" }),
      ev("text_delta", { turn_id: "t1", message_id: "m1", text: "Two choices.\n\n```jarvis-a" }, false),
      ev("text_delta", { turn_id: "t1", message_id: "m1", text: "sk\n{\"questions\":" }, false),
    ]);
    const live = turn(tl, 0).blocks[0] as TextBlock;
    expect(live.text).toBe("Two choices.");
    const done = reduceEvents(tl, [
      ev("assistant_text", { turn_id: "t1", message_id: "m1", text: `Two choices.\n\n${BLOCK}` }),
    ]);
    expect((turn(done, 0).blocks[0] as TextBlock).text).toBe("Two choices.");
  });

  it("keeps a deferred card open after its turn and settles it when another turn starts", () => {
    const tl = reduceEvents(EMPTY_TIMELINE, [
      ev("user_message", { text: "set it up" }),
      ev("turn_started", { turn_id: "t1" }),
      ev("assistant_text", { turn_id: "t1", message_id: "m1", text: "Two choices." }),
      ev("turn_finished", { turn_id: "t1", status: "done" }),
      ev("question_required", { turn_id: "t1", question_id: "q1", questions: QUESTIONS, deferred: true }),
    ]);
    const card = turn(tl).blocks.find((b): b is ToolBlock => b.kind === "tool")?.question;
    expect(card).toMatchObject({ questionId: "q1", deferred: true, closed: false });

    const moved = reduceEvents(tl, [ev("user_message", { text: "never mind" }), ev("turn_started", { turn_id: "t2" })]);
    const settled = turn(moved).blocks.find((b): b is ToolBlock => b.kind === "tool")?.question;
    expect(settled).toMatchObject({ closed: true, answers: [{ source: "closed" }] });
  });

  it("attaches Claude's own question tool to its card", () => {
    const tl = reduceEvents(EMPTY_TIMELINE, [
      ev("turn_started", { turn_id: "t1" }),
      ev("tool_call", { turn_id: "t1", call_id: "c1", name: "AskUserQuestion", input: { questions: QUESTIONS } }),
      ev("question_required", { turn_id: "t1", question_id: "q1", questions: QUESTIONS }),
    ]);
    const tools = turn(tl, 0).blocks.filter((b): b is ToolBlock => b.kind === "tool");
    expect(tools).toHaveLength(1);
    expect(tools[0]).toMatchObject({ callId: "c1", question: { questionId: "q1" } });
  });

  it("carries a plan card until the person answers it or moves on", () => {
    const tl = reduceEvents(EMPTY_TIMELINE, [
      ev("turn_started", { turn_id: "t1" }),
      ev("turn_finished", { turn_id: "t1", status: "done" }),
      ev("plan_ready", { turn_id: "t1", build_mode: "auto" }),
    ]);
    expect(turn(tl, 0).plan).toEqual({ buildMode: "auto", decision: null });
    expect(turn(reduceEvents(tl, [ev("plan_resolved", { turn_id: "t1", decision: "build" })]), 0).plan)
      .toEqual({ buildMode: "auto", decision: "build" });
    expect(turn(reduceEvents(tl, [ev("turn_started", { turn_id: "t2" })]), 0).plan)
      .toEqual({ buildMode: "auto", decision: "superseded" });
  });
});
