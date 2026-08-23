import { describe, expect, it } from "vitest";

import { hintFor, recentLines, waveformPhase } from "@/components/home/VoiceStage";
import { stateKey } from "@/components/home/JarvisBar";
import { greetingKey } from "@/components/home/Greeting";
import type { TranscriptTextLine } from "@/lib/homeTranscript";

const t = (key: string) => key;

function line(who: TranscriptTextLine["who"], text: string, i: number): TranscriptTextLine {
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

describe("JarvisBar state word", () => {
  it("says offline before anything else, connecting over a stale state", () => {
    expect(stateKey("speaking", false, false)).toBe("offline");
    expect(stateKey("idle", true, true)).toBe("connecting");
    expect(stateKey("listening", false, true)).toBe("listening");
    expect(stateKey("speaking", false, true)).toBe("speaking");
  });
});
