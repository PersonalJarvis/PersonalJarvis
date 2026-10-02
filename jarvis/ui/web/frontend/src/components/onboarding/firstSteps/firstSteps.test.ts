import { afterEach, describe, expect, it } from "vitest";
import type { EventItem } from "@/store/events";
import { freshState, nextOpenQuest, questDone, QUESTS, readFirstSteps, writeFirstSteps, type DetectInput, type QuestId } from "./firstSteps";

const quest = (id: QuestId) => QUESTS.find((q) => q.id === id)!;

function input(over: Partial<DetectInput> = {}): DetectInput {
  return { events: [], messages: [], voiceState: "idle", activeSection: "chats", since: 1_000, ...over };
}

const ev = (name: string, ts: number, payload?: unknown): EventItem => ({ id: `${name}-${ts}`, name, ts, payload });

afterEach(() => window.localStorage.clear());

describe("first steps", () => {
  it("has ten quests, each with a way to try it", () => {
    expect(QUESTS).toHaveLength(10);
    expect(new Set(QUESTS.map((q) => q.id)).size).toBe(10);
  });

  it("counts only what happened after the quest was armed", () => {
    const old = input({ events: [ev("ToolCallStarted", 500)] });
    expect(questDone(quest("tool"), old)).toBe(false);
    const fresh = input({ events: [ev("ToolCallStarted", 1_500)] });
    expect(questDone(quest("tool"), fresh)).toBe(true);
  });

  it("needs the right tool, not just any tool", () => {
    const other = input({ events: [ev("ToolCallStarted", 2_000, { tool: "navigate" })] });
    expect(questDone(quest("screen"), other)).toBe(false);
    const screen = input({ events: [ev("ToolCallStarted", 2_000, { tool: "screen-snapshot" })] });
    expect(questDone(quest("screen"), screen)).toBe(true);
    expect(questDone(quest("memory"), input({ events: [ev("WikiPageChanged", 2_000)] }))).toBe(true);
    expect(questDone(quest("agent"), input({ events: [ev("JarvisAgentTaskStarted", 2_000)] }))).toBe(true);
    expect(questDone(quest("artifact"), input({ events: [ev("ToolCallStarted", 2_000, { tool: "create-artifact" })] }))).toBe(true);
  });

  it("sees a reply, a woken voice loop and a visited section", () => {
    const reply = input({ messages: [{ id: "m", role: "assistant", content: "hi", ts: 2_000 }] });
    expect(questDone(quest("ask"), reply)).toBe(true);
    expect(questDone(quest("wake"), input({ voiceState: "listening" }))).toBe(true);
    expect(questDone(quest("wake"), input({ voiceState: "idle" }))).toBe(false);
    expect(questDone(quest("ide"), input({ activeSection: "agentic-ide" }))).toBe(true);
    expect(questDone(quest("plugin"), input({ activeSection: "chats" }))).toBe(false);
  });

  it("moves on to the next quest that is neither done nor skipped", () => {
    expect(nextOpenQuest({ done: [], skipped: [] }, "wake")).toBe("ask");
    expect(nextOpenQuest({ done: ["ask"], skipped: ["tool"] }, "wake")).toBe("screen");
    expect(nextOpenQuest({ done: [], skipped: [] }, "ide")).toBeNull();
  });

  it("keeps its progress across a reload and ignores a broken record", () => {
    expect(readFirstSteps()).toBeNull();
    writeFirstSteps({ ...freshState(), done: ["wake"], current: "ask" });
    expect(readFirstSteps()).toMatchObject({ status: "active", done: ["wake"], current: "ask" });
    window.localStorage.setItem("jarvis.firstSteps.v1", "{not json");
    expect(readFirstSteps()).toBeNull();
  });
});
