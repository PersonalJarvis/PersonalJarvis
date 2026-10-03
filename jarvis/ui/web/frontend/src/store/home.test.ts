import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { reduceLiveReply, useHomeStore } from "@/store/home";
import { useEventStore } from "@/store/events";
import { useAgentChatStore } from "@/store/agentChat";

describe("voice conversation boundaries", () => {
  beforeEach(() => {
    useHomeStore.getState().resetTranscript();
    useHomeStore.setState({ continuedVoiceId: null, freshVoicePending: false, voiceSwitchStopping: false, voiceSelectionPending: false });
    useEventStore.setState({ activeKind: "voice", activeThreadId: null });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ providers: [], sessions: [] }))));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the running call's history row independent of later selections", () => {
    useHomeStore.setState({ continuedVoiceId: "archive" });
    useEventStore.setState({ activeKind: "voice", activeThreadId: "archive" });
    useHomeStore.getState().ingest("VoiceSessionStarted", { session_id: "call" }, 1);
    useHomeStore.getState().setContinuedVoiceId("another-archive");
    expect(useHomeStore.getState().liveConversationId).toBe("archive");
  });

  it("does not attach a fresh call to a stale archive selection", () => {
    useHomeStore.setState({ continuedVoiceId: "old-archive" });
    useEventStore.setState({ activeKind: "voice", activeThreadId: null });
    useHomeStore.getState().ingest("VoiceSessionStarted", { session_id: "fresh-call" }, 1);
    expect(useHomeStore.getState().liveConversationId).toBe("fresh-call");
  });

  it.each(["hotkey", "voice_pattern", "client_stop", "idle_timeout"])("starts a fresh lane after %s", (hangup_reason) => {
    const home = useHomeStore.getState();
    home.setJarvisCardMode("chat");
    useEventStore.setState({ activeKind: "voice", activeThreadId: "first", transcription: "stale preview", transcriptionFinal: false });
    home.ingest("TranscriptFinal", { transcript: { text: "Old conversation" } }, 1);
    home.ingest("AssistantTextDelta", { channel: "voice", text: "Partial reply" }, 2);
    expect(useHomeStore.getState().transcript.length).toBeGreaterThan(0);
    home.ingest("VoiceSessionEnded", { session_id: "first", hangup_reason }, 3);
    expect(useHomeStore.getState().transcript).toEqual([]);
    expect(useHomeStore.getState().liveReply).toBe("");
    expect(useHomeStore.getState().jarvisCardMode).toBe("voice");
    expect(useHomeStore.getState().freshVoicePending).toBe(true);
    expect(useEventStore.getState().activeThreadId).toBeNull();
    expect(useEventStore.getState().transcription).toBe("");
    home.ingest("VoiceSessionStarted", { session_id: "second" }, 4);
    expect(useHomeStore.getState().freshVoicePending).toBe(false);
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
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(["hotkey", "voice_pattern", "client_stop", "idle_timeout"])("leaves a continued archive after %s and unbinds the next wake", (hangup_reason) => {
    const home = useHomeStore.getState();
    home.setContinuedVoiceId("archive");
    useEventStore.setState({ activeKind: "voice", activeThreadId: "archive" });
    home.seedTranscript([{ id: "old", who: "user", text: "Saved conversation", ts: 1 }]);
    home.ingest("VoiceSessionStarted", { session_id: "call" }, 2);
    home.ingest("VoiceSessionEnded", { session_id: "call", hangup_reason }, 3);
    expect(useHomeStore.getState().continuedVoiceId).toBeNull();
    expect(useHomeStore.getState().freshVoicePending).toBe(true);
    expect(useEventStore.getState().activeThreadId).toBeNull();
    expect(useHomeStore.getState().transcript).toEqual([]);
    expect(fetch).toHaveBeenCalledWith("/api/agent-chat/voice-chat", expect.objectContaining({
      method: "PUT", body: JSON.stringify({ session_id: null, voice_session_id: null }),
    }));
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith("/resume"))).toBe(false);
    home.ingest("VoiceTranscriptUpdated", { session_id: "call", segment_id: "late", role: "assistant", text: "Late final", revision: 2 }, 4);
    home.ingest("SpeechSpoken", { text: "Late spoken reply", spoken_kind: "reply" }, 4);
    home.ingest("AssistantTextDelta", { channel: "voice", text: "Late snapshot" }, 4);
    expect(useHomeStore.getState().transcript).toEqual([]);
    expect(useHomeStore.getState().liveReply).toBe("");
    home.ingest("VoiceSessionStarted", { session_id: "next-call" }, 5);
    home.ingest("TranscriptFinal", { transcript: { text: "Fresh question" } }, 6);
    expect(useHomeStore.getState().transcript.map(line => line.text)).toEqual(["Fresh question"]);
  });

  it("clears the selected typed timeline when its voice call ends without deleting history", () => {
    const session = { session_id: "typed" };
    useAgentChatStore.setState({ activeSessionId: session.session_id });
    useEventStore.setState({ activeKind: "text", activeThreadId: "typed" });
    useHomeStore.getState().ingest("VoiceSessionEnded", { hangup_reason: "hotkey" }, 1);
    expect(useAgentChatStore.getState().activeSessionId).toBeNull();
    expect(useAgentChatStore.getState().timeline.items).toEqual([]);
    expect(useEventStore.getState().activeThreadId).toBeNull();
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
  });

  it("preserves an explicit archive switch while the previous call is stopping", () => {
    useEventStore.setState({ activeKind: "voice", activeThreadId: "chosen" });
    useHomeStore.setState({ continuedVoiceId: "chosen", voiceSwitchStopping: true, voiceSelectionPending: true });
    useHomeStore.getState().ingest("VoiceSessionEnded", { hangup_reason: "hotkey" }, 1);
    expect(useEventStore.getState().activeThreadId).toBe("chosen");
    expect(useHomeStore.getState().continuedVoiceId).toBe("chosen");
    expect(useHomeStore.getState().freshVoicePending).toBe(false);
    expect(useHomeStore.getState().voiceSelectionPending).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
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
