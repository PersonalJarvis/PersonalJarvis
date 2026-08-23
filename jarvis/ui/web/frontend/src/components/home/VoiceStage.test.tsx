import { describe, expect, it } from "vitest";

import { hintFor, recentLines, waveformPhase } from "@/components/home/VoiceStage";
import { greetingKey } from "@/components/home/Greeting";
import type { ChatMessage } from "@/store/events";

const t = (key: string) => key;

function msg(role: ChatMessage["role"], content: string, i: number): ChatMessage {
  return { id: `m${i}`, role, content, ts: i };
}

describe("VoiceStage helpers", () => {
  it("keeps only the last spoken/typed lines, in order", () => {
    const all: ChatMessage[] = [
      msg("system", "boot", 0),
      msg("user", "one", 1),
      msg("preamble", "pre", 2),
      msg("assistant", "two", 3),
      msg("user", "three", 4),
      msg("assistant", "four", 5),
    ];
    expect(recentLines(all, 3).map((m) => m.content)).toEqual(["two", "three", "four"]);
    expect(recentLines(all, 10).map((m) => m.content)).toEqual(["one", "two", "three", "four"]);
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
