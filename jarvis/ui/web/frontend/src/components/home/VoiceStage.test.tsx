import { describe, expect, it } from "vitest";

import { hintFor, recentLines, waveformPhase } from "@/components/home/VoiceStage";
import { greetingKey } from "@/components/home/Greeting";
import { reduceTranscript, type TranscriptLine } from "@/lib/homeTranscript";

const t = (key: string) => key;

function line(who: TranscriptLine["who"], text: string, i: number): TranscriptLine {
  return { id: `m${i}`, who, text, ts: i };
}

describe("VoiceStage helpers", () => {
  it("keeps only the last lines, in order", () => {
    const all = [line("user", "one", 1), line("assistant", "two", 2), line("user", "three", 3)];
    expect(recentLines(all, 2).map((m) => m.text)).toEqual(["two", "three"]);
    expect(recentLines(all, 10).map((m) => m.text)).toEqual(["one", "two", "three"]);
  });

  it("maps the voice state onto the waveform phases, idle when offline", () => {
    expect(waveformPhase("listening", false)).toBe("idle");
    expect(waveformPhase("listening", true)).toBe("listening");
    expect(waveformPhase("thinking", true)).toBe("working");
    expect(waveformPhase("speaking", true)).toBe("speaking");
    expect(waveformPhase("error", true)).toBe("error");
    expect(waveformPhase("paused", true)).toBe("idle");
  });

  it("names the wake phrase in the idle hint and the state otherwise", () => {
    const base = { connected: true, warming: false, connecting: false, t };
    expect(hintFor({ ...base, voiceState: "idle", wakePhrase: "Hey Nova" })).toBe("home.hint_idle");
    expect(hintFor({ ...base, voiceState: "idle", wakePhrase: "" })).toBe("home.hint_idle_nowake");
    expect(hintFor({ ...base, voiceState: "listening", wakePhrase: "" })).toBe("home.hint_listening");
    expect(hintFor({ ...base, voiceState: "speaking", wakePhrase: "" })).toBe("home.hint_speaking");
    expect(hintFor({ ...base, connecting: true, voiceState: "idle", wakePhrase: "" })).toBe(
      "home.hint_connecting",
    );
    expect(hintFor({ ...base, connected: false, voiceState: "idle", wakePhrase: "" })).toBe(
      "home.hint_offline",
    );
    expect(
      hintFor({ ...base, connected: false, warming: true, voiceState: "idle", wakePhrase: "" }),
    ).toBe("home.hint_warming");
  });

  it("greets by the hour", () => {
    expect(greetingKey(8)).toBe("home.greeting_morning");
    expect(greetingKey(13)).toBe("home.greeting_afternoon");
    expect(greetingKey(21)).toBe("home.greeting_evening");
  });
});

describe("homeTranscript reducer", () => {
  it("turns heard words and spoken answers into lines, joining the answer's pieces", () => {
    let lines: TranscriptLine[] = [];
    lines = reduceTranscript(lines, "TranscriptFinal", { transcript: { text: "  what time is it " } }, 1000);
    // The final TranscriptionUpdate of the same utterance is not a second line.
    lines = reduceTranscript(lines, "TranscriptionUpdate", { text: "what time is it", is_final: true }, 1200);
    lines = reduceTranscript(lines, "TranscriptionUpdate", { text: "what time", is_final: false }, 1300);
    lines = reduceTranscript(lines, "SpeechSpoken", { text: "It is ten past nine." }, 2000);
    lines = reduceTranscript(lines, "SpeechSpoken", { text: "Shall I set a timer?" }, 3000);
    expect(lines.map((l) => [l.who, l.text])).toEqual([
      ["user", "what time is it"],
      ["assistant", "It is ten past nine. Shall I set a timer?"],
    ]);
  });

  it("takes typed turns from MessageSent and ignores everything else", () => {
    let lines: TranscriptLine[] = [];
    const same = lines;
    lines = reduceTranscript(lines, "BrainTurnCompleted", { tokens_in: 1 }, 1);
    expect(lines).toBe(same);
    lines = reduceTranscript(lines, "MessageSent", { role: "user", text: "hello" }, 10);
    lines = reduceTranscript(lines, "MessageSent", { role: "system", text: "note" }, 11);
    lines = reduceTranscript(lines, "MessageSent", { role: "assistant", text: "hi there" }, 12);
    // A reply that is also spoken is one line, not two.
    lines = reduceTranscript(lines, "SpeechSpoken", { text: "hi there" }, 13);
    expect(lines.map((l) => [l.who, l.text])).toEqual([
      ["user", "hello"],
      ["assistant", "hi there"],
    ]);
  });
});
