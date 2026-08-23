import { describe, expect, it } from "vitest";

import { reduceLiveReply } from "@/store/home";

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
