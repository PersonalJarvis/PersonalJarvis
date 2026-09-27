import { expect, test } from "vitest";
import type { AgentChatEvent } from "@/lib/agentChatApi";
import { legacyRoutineEvents, routineTask, routineTaskId, type RoutineChatTarget } from "./routineExecution";

export const instruction = (id: string) => `Scheduled routine ${id}. Follow your CURRENT standing instructions and permissions.\nUse your memory and conversation archive for prior results. For information watches, check sources and dates, remember last-seen items, and report only meaningful new findings.\n\nCheck this run.`;

function run(seq: number, start: number, id: string, answer: string): AgentChatEvent[] {
  const turn = `turn-${seq}`;
  return [
    { seq, ts_ms: start, kind: "user_message", payload: { text: instruction(id) } },
    { seq: seq + 1, ts_ms: start + 10, kind: "turn_started", payload: { turn_id: turn } },
    { seq: seq + 2, ts_ms: start + 20, kind: "tool_call", payload: { turn_id: turn, name: "read", call_id: turn } },
    { seq: seq + 3, ts_ms: start + 30, kind: "assistant_text", payload: { turn_id: turn, text: answer } },
    { seq: seq + 4, ts_ms: start + 1000, kind: "turn_finished", payload: { turn_id: turn, status: "done" } },
  ];
}

const target: RoutineChatTarget = { agentId: "mail", sessionId: "society:mail", title: "Inbox", timestamp: 10000,
  legacy: { taskId: "daily", startedMs: 9900, finishedMs: 11100 } };
const first = run(1, 10000, "daily", "FIRST RUN");
const second = run(10, 100000, "daily", "SECOND RUN");

test("history selects the matching execution, including tools, without other conversations", () => {
  expect(legacyRoutineEvents([...first, ...second], target)).toEqual(first);
});

test("routine-check navigation uses the exact message even for an identical later prompt", () => {
  expect(legacyRoutineEvents([...first, ...second], { ...target, legacy: { taskId: "daily", messageId: "u-10" } })).toEqual(second);
});

test("missing and ambiguous executions never fall back to a different run", () => {
  expect(legacyRoutineEvents(second, target)).toEqual([]);
  expect(legacyRoutineEvents([...first, ...run(20, 10500, "daily", "ambiguous")], target)).toEqual([]);
  expect(legacyRoutineEvents(first, { ...target, legacy: { taskId: "other", messageId: "u-1" } })).toEqual([]);
});

test("old result-only steps match their completion time, not the latest prompt", () => {
  expect(legacyRoutineEvents([...first, ...second], { ...target, legacy: { taskId: "daily", finishedMs: 11020 } })).toEqual(first);
});

test("ordinary messages and notices cannot become execution links", () => {
  expect(routineTaskId("Scheduled routine daily. Explain this text.")).toBeNull();
  expect(routineTask(instruction("daily"))).toBe("Check this run.");
  const notice: AgentChatEvent = { seq: 200, ts_ms: 10500, kind: "notice", payload: { text: "PRIVATE unrelated notice" } };
  expect(legacyRoutineEvents([...first.slice(0, 2), notice, ...first.slice(2), ...second], target)).toEqual(first);
});
