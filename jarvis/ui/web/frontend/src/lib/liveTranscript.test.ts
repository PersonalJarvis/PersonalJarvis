import { describe, expect, it } from "vitest";
import { reduceTranscript, type TranscriptLine } from "./homeTranscript";
import { useHomeStore } from "@/store/home";
import { useEventStore } from "@/store/events";

function caption(segment_id: string, role: "user" | "assistant", text: string, revision = 1) {
  return { session_id: "call", segment_id, role, text, start_ms: 0, end_ms: 500, revision };
}

describe("continuous voice conversation", () => {
  it("does not mark delegated work complete when the user interrupts speech", () => {
    let lines = reduceTranscript([], "VoiceTranscriptUpdated", caption("u", "user", "Read settings"), 1);
    lines = reduceTranscript(lines, "BrainTurnStarted", { model: "chosen" }, 2);
    lines = reduceTranscript(lines, "SystemStateChanged", { previous: "SPEAKING", new_state: "LISTENING" }, 3);
    const trace = lines.find(line => line.who === "steps");
    expect(trace?.who === "steps" && trace.live).toBe(true);
    expect(trace?.who === "steps" && trace.steps[0].status).toBe("active");
  });
  it("keeps user, trace and assistant rows while successive snapshots grow in place", () => {
    let lines: TranscriptLine[] = [];
    lines = reduceTranscript(lines, "VoiceTranscriptUpdated", caption("u1", "user", "Read settings"), 1);
    lines = reduceTranscript(lines, "BrainTurnStarted", { model: "chosen" }, 2);
    lines = reduceTranscript(lines, "ReasoningSummaryUpdated", { response_id: "r", text: "Checking the setting", done: true }, 3);
    lines = reduceTranscript(lines, "BrainTurnCompleted", { model: "chosen" }, 4);
    lines = reduceTranscript(lines, "VoiceTranscriptUpdated", caption("a1", "assistant", "It is"), 5);
    lines = reduceTranscript(lines, "VoiceTranscriptUpdated", caption("a1", "assistant", "It is enabled", 2), 6);
    lines = reduceTranscript(lines, "VoiceTranscriptUpdated", caption("u2", "user", "Read settings"), 7);
    lines = reduceTranscript(lines, "VoiceTranscriptUpdated", caption("a2", "assistant", "Still enabled"), 8);
    expect(lines.map(line => line.who)).toEqual(["user", "steps", "assistant", "user", "assistant"]);
    expect(lines[2].text).toBe("It is enabled");
    const duplicate = reduceTranscript(lines, "VoiceTranscriptUpdated", caption("a1", "assistant", "old", 1), 9);
    expect(duplicate).toBe(lines);
    const trace = lines[1];
    expect(trace.who === "steps" && trace.steps.some(step => step.detail === "Checking the setting")).toBe(true);
  });

  it("ignores late captions and hangups from the previous session", () => {
    const home = useHomeStore.getState();
    home.resetTranscript();
    home.ingest("VoiceSessionStarted", { session_id: "call" }, 1);
    useEventStore.getState().setTranscription("legacy preview", false);
    home.ingest("VoiceTranscriptUpdated", caption("u", "user", "Hello"), 2);
    expect(useEventStore.getState().transcription).toBe("");
    home.ingest("VoiceTranscriptUpdated", { ...caption("old", "user", "Stale"), session_id: "old" }, 3);
    home.ingest("VoiceSessionEnded", { session_id: "old" }, 4);
    expect(useHomeStore.getState().transcript.map(line => line.text)).toEqual(["Hello"]);
    home.resetTranscript();
  });
});
