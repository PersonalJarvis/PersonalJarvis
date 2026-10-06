import { describe, expect, it } from "vitest";

import type { AgentChatEvent } from "@/lib/agentChatApi";
import { hideMeetingPass, MEETING_PASS } from "./meetingPass";
import { EMPTY_TIMELINE, reduceEvents, type TurnItem } from "./reduce";

function ev(seq: number, kind: string, payload: Record<string, unknown>): AgentChatEvent {
  return { seq, ts_ms: 1_000 + seq, kind, payload };
}

describe("meeting silence marker", () => {
  it("hides the marker alone, finished or still streaming, and nothing else", () => {
    expect(hideMeetingPass(`  ${MEETING_PASS}\n`)).toBe("");
    expect(hideMeetingPass("[[MEETING_")).toBe("");
    expect(hideMeetingPass("A real answer")).toBe("A real answer");
    expect(hideMeetingPass(`${MEETING_PASS} but more`)).toBe(`${MEETING_PASS} but more`);
    expect(hideMeetingPass("[[Wiki link]]")).toBe("[[Wiki link]]");
  });

  it("shows a silent meeting turn as an empty reply in the agent's own chat", () => {
    const tl = reduceEvents(EMPTY_TIMELINE, [
      ev(1, "user_message", { text: "prompt", typed: "Compare ideas" }),
      ev(2, "turn_started", { turn_id: "t1", provider: "ollama", model: "m", effort: "", runner: "api" }),
      ev(3, "assistant_text", { turn_id: "t1", message_id: "m1", text: MEETING_PASS }),
      ev(4, "turn_finished", { turn_id: "t1", status: "done", duration_ms: 5, usage: {} }),
    ]);
    const turn = tl.items[1] as TurnItem;
    expect(turn.blocks.every((b) => b.kind !== "text" || b.text === "")).toBe(true);
  });
});
