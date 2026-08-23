import { describe, expect, it } from "vitest";

import {
  LATE_ATTACH_MS,
  STALE_TURN_MS,
  reduceTranscript,
  type TranscriptLine,
  type TranscriptStepsLine,
} from "@/lib/homeTranscript";
import { MAX_THINKING_STEPS } from "@/lib/thinkingSteps";

type Step = [name: string, payload: unknown, ts: number];

function run(steps: Step[], start: TranscriptLine[] = []): TranscriptLine[] {
  return steps.reduce((lines, [name, payload, ts]) => reduceTranscript(lines, name, payload, ts), start);
}

function shape(lines: TranscriptLine[]): unknown[] {
  return lines.map((l) =>
    l.who === "steps"
      ? ["steps", l.steps.map((s) => `${s.kind}:${s.detail ?? ""}:${s.status}`), l.live ? "live" : "done"]
      : [l.who, l.text],
  );
}

function stepsLine(lines: TranscriptLine[]): TranscriptStepsLine {
  const found = lines.find((l): l is TranscriptStepsLine => l.who === "steps");
  if (!found) throw new Error("no steps line");
  return found;
}

describe("homeTranscript reducer — text", () => {
  it("turns heard words and spoken answers into lines, joining the answer's pieces", () => {
    const lines = run([
      ["TranscriptFinal", { transcript: { text: "  what time is it " } }, 1000],
      // The final TranscriptionUpdate of the same utterance is not a second line.
      ["TranscriptionUpdate", { text: "what time is it", is_final: true }, 1200],
      ["TranscriptionUpdate", { text: "what time", is_final: false }, 1300],
      ["SpeechSpoken", { text: "It is ten past nine." }, 2000],
      ["SpeechSpoken", { text: "Shall I set a timer?" }, 3000],
    ]);
    expect(shape(lines)).toEqual([
      ["user", "what time is it"],
      ["assistant", "It is ten past nine. Shall I set a timer?"],
    ]);
  });

  it("takes typed turns from MessageSent and ignores everything else", () => {
    let lines: TranscriptLine[] = [];
    const same = lines;
    lines = reduceTranscript(lines, "BrainTurnCompleted", { tokens_in: 1 }, 1);
    expect(lines).toBe(same);
    lines = reduceTranscript(lines, "SystemStateChanged", { new_state: "LISTENING" }, 2);
    expect(lines).toBe(same);
    lines = run(
      [
        ["MessageSent", { role: "user", text: "hello" }, 10],
        ["MessageSent", { role: "system", text: "note" }, 11],
        ["MessageSent", { role: "assistant", text: "hi there" }, 12],
        // A reply that is also spoken is one line, not two.
        ["SpeechSpoken", { text: "hi there" }, 13],
      ],
      lines,
    );
    expect(shape(lines)).toEqual([
      ["user", "hello"],
      ["assistant", "hi there"],
    ]);
  });
});

describe("homeTranscript reducer — steps per turn", () => {
  it("collects a tool call into the turn between the words and the answer", () => {
    const lines = run([
      ["TranscriptFinal", { transcript: { text: "search my mail for the invoice" } }, 1000],
      ["BrainTurnStarted", { provider: "openai", model: "gpt-5" }, 1400],
      ["ActionProposed", { tool_name: "gmail_search", tier: "safe" }, 1500],
      ["ActionExecuted", { tool_name: "gmail_search", success: true, duration_ms: 820 }, 2320],
      ["SpeechSpoken", { text: "Found it — sent on Tuesday.", spoken_kind: "reply" }, 3000],
      ["BrainTurnCompleted", { provider: "openai", model: "gpt-5" }, 3100],
    ]);
    expect(shape(lines)).toEqual([
      ["user", "search my mail for the invoice"],
      ["steps", ["brain:openai · gpt-5:done", "tool:gmail_search:done"], "done"],
      ["assistant", "Found it — sent on Tuesday."],
    ]);
    const turn = stepsLine(lines);
    // The reply closed the turn; the brain's late Completed attached quietly.
    expect(turn.live).toBe(false);
    // Thinking time runs from the user's words, not from the first step.
    expect(turn.startedTs).toBe(1000);
    expect(turn.durationMs).toBe(2000);
    expect(turn.steps[1].durationMs).toBe(820);
  });

  it("is live while the turn runs and shows the active tool", () => {
    const open = run([
      ["TranscriptFinal", { transcript: { text: "open the calendar" } }, 1000],
      ["ActionProposed", { tool_name: "computer_use" }, 1500],
    ]);
    const turn = stepsLine(open);
    expect(turn.live).toBe(true);
    expect(turn.durationMs).toBeUndefined();
    expect(turn.steps.map((s) => s.status)).toEqual(["active"]);
  });

  it("handles the classic pipeline order: tools first, brain events together, then speech", () => {
    const lines = run([
      ["TranscriptFinal", { transcript: { text: "what is on my calendar" } }, 1000],
      ["ActionProposed", { tool_name: "calendar_list" }, 1600],
      ["ActionExecuted", { tool_name: "calendar_list", success: true }, 2100],
      // The pipeline publishes both only after the brain call succeeded.
      ["BrainTurnStarted", { provider: "anthropic", model: "claude" }, 4000],
      ["BrainTurnCompleted", { provider: "anthropic", model: "claude" }, 4001],
      ["SpeechSpoken", { text: "Two meetings.", spoken_kind: "reply" }, 4500],
    ]);
    expect(shape(lines)).toEqual([
      ["user", "what is on my calendar"],
      ["steps", ["tool:calendar_list:done", "brain:anthropic · claude:done"], "done"],
      ["assistant", "Two meetings."],
    ]);
    const turn = stepsLine(lines);
    // The brain step is backdated to the tool's end, not a 1 ms blip.
    expect(turn.steps[1].startedTs).toBe(2100);
    expect(turn.steps[1].durationMs).toBe(1901);
    expect(turn.durationMs).toBe(3001);
  });

  it("keeps a tool-only turn that never speaks", () => {
    const lines = run([
      ["TranscriptFinal", { transcript: { text: "mute the music" } }, 1000],
      ["ActionProposed", { tool_name: "media_control" }, 1300],
      ["ActionExecuted", { tool_name: "media_control", success: true }, 1400],
      ["BrainTurnStarted", { provider: "x", model: "y" }, 1500],
      ["BrainTurnCompleted", { provider: "x", model: "y" }, 1500],
      // The floor comes back without a word being said.
      ["SystemStateChanged", { new_state: "LISTENING", previous: "THINKING" }, 1600],
    ]);
    expect(shape(lines)).toEqual([
      ["user", "mute the music"],
      ["steps", ["tool:media_control:done", "brain:x · y:done"], "done"],
    ]);
    expect(stepsLine(lines).live).toBe(false);
  });

  it("lets a preamble be spoken mid-turn without closing it, and a late step join", () => {
    const lines = run([
      ["TranscriptFinal", { transcript: { text: "book the table" } }, 1000],
      ["ActionProposed", { tool_name: "browser" }, 1200],
      ["SpeechSpoken", { text: "One moment.", spoken_kind: "preamble" }, 1300],
      ["ActionExecuted", { tool_name: "browser", success: true }, 5000],
      ["BrainTurnCompleted", {}, 5100],
      ["SpeechSpoken", { text: "Done, eight o'clock.", spoken_kind: "reply" }, 5500],
      // A trailing tool report still lands on the same turn (the turn's last
      // event was the brain completing at 5100).
      ["ActionExecuted", { tool_name: "notify", success: false }, 5100 + LATE_ATTACH_MS - 1],
    ]);
    expect(shape(lines)).toEqual([
      ["user", "book the table"],
      ["steps", ["tool:browser:done", "tool:notify:error"], "done"],
      ["assistant", "One moment."],
      ["assistant", "Done, eight o'clock."],
    ]);
  });

  it("opens a fresh turn for steps long after the last one and closes a stale turn", () => {
    const lines = run([
      ["TranscriptFinal", { transcript: { text: "start the report" } }, 1000],
      ["JarvisAgentTaskStarted", { utterance: "start the report" }, 1500],
      // Nothing for a long time — the worker never reported back.
      ["ActionExecuted", { tool_name: "notify", success: true }, 1500 + STALE_TURN_MS + 1],
    ]);
    expect(shape(lines)).toEqual([
      ["user", "start the report"],
      ["steps", ["worker:start the report:done"], "done"],
      ["steps", ["tool:notify:done"], "done"],
    ]);
    const [first, second] = lines.filter((l): l is TranscriptStepsLine => l.who === "steps");
    // Closed when it went quiet, not two minutes later.
    expect(first.durationMs).toBe(500);
    expect(second.startedTs).toBe(1500 + STALE_TURN_MS + 1);
  });

  it("closes the open turn when the user speaks again", () => {
    const lines = run([
      ["TranscriptFinal", { transcript: { text: "first" } }, 1000],
      ["ActionProposed", { tool_name: "slow_tool" }, 1200],
      ["TranscriptFinal", { transcript: { text: "never mind" } }, 3000],
    ]);
    expect(shape(lines)).toEqual([
      ["user", "first"],
      ["steps", ["tool:slow_tool:done"], "done"],
      ["user", "never mind"],
    ]);
  });

  it("returns the same array for events that change nothing", () => {
    const lines = run([
      ["TranscriptFinal", { transcript: { text: "hi" } }, 1000],
      ["ActionProposed", { tool_name: "t" }, 1100],
    ]);
    expect(reduceTranscript(lines, "AudioLevel", { input: 0.2 }, 1200)).toBe(lines);
    // Completing a worker that never started touches nothing.
    expect(reduceTranscript(lines, "JarvisAgentTaskCompleted", { success: true }, 1200)).toBe(lines);
    // A realtime BrainTurnCompleted with no brain step still ends the turn.
    const closed = reduceTranscript(lines, "BrainTurnCompleted", { finish_reason: "realtime_usage" }, 1300);
    expect(closed).not.toBe(lines);
    expect(stepsLine(closed).live).toBe(false);
    expect(stepsLine(closed).steps[0].status).toBe("done");
  });

  it("caps the steps of one turn", () => {
    const events: Step[] = [["TranscriptFinal", { transcript: { text: "many" } }, 1000]];
    for (let i = 0; i < MAX_THINKING_STEPS + 10; i++) {
      events.push(["ActionExecuted", { tool_name: `tool_${i}`, success: true }, 1100 + i]);
    }
    const lines = run(events);
    expect(stepsLine(lines).steps.length).toBe(MAX_THINKING_STEPS);
  });
});
