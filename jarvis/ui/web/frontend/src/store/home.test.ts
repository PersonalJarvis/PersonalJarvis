import { beforeEach, describe, expect, it } from "vitest";

import { reduceLiveReply, useHomeStore } from "@/store/home";

describe("voice conversation boundaries", () => {
  beforeEach(() => useHomeStore.getState().resetTranscript());

  it.each(["hotkey", "voice_pattern", "client_stop", "idle_timeout"])("starts a fresh lane after %s", (hangup_reason) => {
    const home = useHomeStore.getState();
    home.ingest("TranscriptFinal", { transcript: { text: "Old conversation" } }, 1);
    home.ingest("AssistantTextDelta", { channel: "voice", text: "Partial reply" }, 2);
    expect(useHomeStore.getState().transcript.length).toBeGreaterThan(0);
    home.ingest("VoiceSessionEnded", { session_id: "first", hangup_reason }, 3);
    expect(useHomeStore.getState().transcript).toEqual([]);
    expect(useHomeStore.getState().liveReply).toBe("");
    home.ingest("VoiceSessionStarted", { session_id: "second" }, 4);
    home.ingest("TranscriptFinal", { transcript: { text: "New conversation" } }, 5);
    expect(useHomeStore.getState().transcript.map((line) => line.text)).toEqual(["New conversation"]);
  });

  it("keeps the conversation between turns and during provider fallback", () => {
    const home = useHomeStore.getState();
    home.ingest("TranscriptFinal", { transcript: { text: "Keep this turn" } }, 1);
    home.ingest("VoiceTurnCompleted", {}, 2);
    home.ingest("SystemStateChanged", { new_state: "LISTENING" }, 3);
    home.ingest("VoiceSessionEnded", { hangup_reason: "realtime_fallback" }, 4);
    home.ingest("VoiceSessionEnded", { hangup_reason: "desktop_fallback" }, 5);
    expect(useHomeStore.getState().transcript.map((line) => line.text)).toEqual(["Keep this turn"]);
  });
});

describe("reduceLiveReply — the voice lane's answer as it forms", () => {
  it("grows with voice/realtime snapshots and ignores the typed chat's", () => {
    let live = reduceLiveReply("", "AssistantTextDelta", { channel: "realtime", text: "Ich" });
    expect(live).toBe("Ich");
    live = reduceLiveReply(live, "AssistantTextDelta", { channel: "voice", text: "Ich schaue" });
    expect(live).toBe("Ich schaue");
    expect(reduceLiveReply(live, "AssistantTextDelta", { channel: "chat", text: "typed" })).toBe(
      "Ich schaue",
    );
  });

  it("goes away once the spoken line, the turn's end or idle replaces it", () => {
    expect(reduceLiveReply("x", "SpeechSpoken", { text: "x", spoken_kind: "reply" })).toBe("");
    expect(reduceLiveReply("x", "SpeechSpoken", { text: "moment", spoken_kind: "preamble" })).toBe(
      "x",
    );
    expect(reduceLiveReply("x", "MessageSent", { role: "assistant", text: "x" })).toBe("");
    expect(reduceLiveReply("x", "MessageSent", { role: "user", text: "y" })).toBe("x");
    expect(reduceLiveReply("x", "VoiceTurnCompleted", {})).toBe("");
    expect(reduceLiveReply("x", "SystemStateChanged", { new_state: "IDLE" })).toBe("");
    expect(reduceLiveReply("x", "SystemStateChanged", { new_state: "SPEAKING" })).toBe("x");
    expect(reduceLiveReply("x", "HotkeyPressed", {})).toBe("x");
  });
});
