/**
 * The lead card's history rail: typed chats and voice sessions as two lists,
 * opening in place — never navigating away from the card.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { JarvisHistoryRail } from "@/components/society/chat/JarvisHistoryRail";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { setJarvisCardMode } from "./AgentChatPanel";
import type { AgentChatSession } from "@/lib/agentChatApi";
import type { ConversationSummary } from "@/store/events";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("./AgentChatPanel", () => ({ setJarvisCardMode: vi.fn() }));

class FakeSocket {
  onopen: (() => void) | null = null;
  onmessage: ((msg: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close() {}
}

function chatSession(over: Partial<AgentChatSession> = {}): AgentChatSession {
  return {
    session_id: "chat-1",
    title: "Typed questions",
    provider: "openai",
    model: "m",
    effort: "low",
    cwd: "",
    permission_mode: "ask",
    surface: "jarvis",
    vendor_session: null,
    created_ms: 1,
    updated_ms: 2,
    message_count: 3,
    preview: "typed preview",
    ...over,
  };
}

function voiceSummary(over: Partial<ConversationSummary> = {}): ConversationSummary {
  return {
    kind: "voice",
    id: "voice-1",
    title: "Evening call",
    preview: "spoken preview",
    created_ms: 1,
    updated_ms: 3,
    message_count: 4,
    ...over,
  };
}

function json(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

describe("JarvisHistoryRail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useHomeStore.setState({ voiceSelectionPending: false, voiceSwitchStopping: false });
    useHomeStore.getState().resetTranscript();
    Element.prototype.scrollIntoView = vi.fn();
    vi.stubGlobal("WebSocket", FakeSocket);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const path = String(url);
        if (path.includes("/api/chats/voice/")) {
          return json({ kind: "voice", id: "voice-1", title: "Evening call", messages: [
            { role: "user", text: "Previous spoken question", ts_ms: 1 },
            { role: "assistant", text: "Previous spoken answer", ts_ms: 2 },
          ] });
        }
        if (path.includes("/api/chats?")) {
          return json([voiceSummary()]);
        }
        if (path.includes("/api/agent-chat/sessions")) {
          if (init?.method === "DELETE") return json({});
          return json({ sessions: [chatSession()] });
        }
        return json({});
      }),
    );
    useAgentChatStore.getState().disconnect();
    useAgentChatStore.setState({
      activeSessionId: null,
      activeSession: null,
      timeline: { items: [], pendingApprovals: [], lastSeq: 0, sessionPatch: null },
      busy: false,
      lastError: null,
      sessions: [],
      socketState: "idle",
    });
    useEventStore.setState({
      conversations: [],
      messages: [],
      activeThreadId: null,
      activeKind: "text",
      voiceState: "idle",
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  test("lists typed chats and voice sessions in two sections", async () => {
    render(<JarvisHistoryRail />);
    expect(await screen.findByTestId("jarvis-history-chat-row")).toBeTruthy();
    expect(await screen.findByTestId("jarvis-history-voice-row")).toBeTruthy();
    expect(screen.getByTestId("jarvis-history-chat-row").textContent).toContain("Typed questions");
    expect(screen.getByTestId("jarvis-history-voice-row").textContent).toContain("Evening call");
  });

  test("opening a chat row shows that session in the column", async () => {
    render(<JarvisHistoryRail />);
    fireEvent.click(await screen.findByTestId("jarvis-history-chat-row"));
    expect(useAgentChatStore.getState().activeSessionId).toBe("chat-1");
  });

  test("opening a voice row clears the chat and marks the thread", async () => {
    useAgentChatStore.setState({ activeSessionId: "chat-1" });
    render(<JarvisHistoryRail />);
    fireEvent.click(await screen.findByTestId("jarvis-history-voice-row"));
    expect(useAgentChatStore.getState().activeSessionId).toBeNull();
    expect(useEventStore.getState().activeThreadId).toBe("voice-1");
    await waitFor(() => expect(useHomeStore.getState().transcript.map((line) => line.text)).toEqual([
      "Previous spoken question", "Previous spoken answer",
    ]));
    expect(setJarvisCardMode).toHaveBeenCalledWith("voice");
  });

  test("a late archive response cannot restore the previous call after hangup", async () => {
    let finish!: (response: Response) => void;
    render(<JarvisHistoryRail />);
    await screen.findByTestId("jarvis-history-voice-row");
    vi.mocked(fetch).mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }));
    fireEvent.click(screen.getByTestId("jarvis-history-voice-row"));
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    await act(async () => {
      useHomeStore.getState().ingest("VoiceSessionEnded", { hangup_reason: "hotkey" }, 5);
      finish(json({ kind: "voice", id: "voice-1", messages: [{ role: "user", text: "Stale archive", ts_ms: 1 }] }));
    });
    await waitFor(() => expect((screen.getByTestId("jarvis-history-voice-row")).getAttribute("data-active")).toBeNull());
    expect(useHomeStore.getState().transcript).toEqual([]);
    expect(useEventStore.getState().messages).toEqual([]);
  });

  test("switching archives ends the active call before resuming and keeps the selected context", async () => {
    useEventStore.setState({ voiceState: "listening" });
    render(<JarvisHistoryRail />);
    fireEvent.click(await screen.findByTestId("jarvis-history-voice-row"));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/voice/hangup", { method: "POST", cache: "no-store" }));
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith("/resume"))).toBe(false);
    expect(useHomeStore.getState().voiceSelectionPending).toBe(true);
    await act(async () => {
      useHomeStore.getState().ingest("VoiceSessionEnded", { hangup_reason: "hotkey" }, 3);
      useEventStore.getState().setVoice("idle");
    });
    await waitFor(() => expect(useHomeStore.getState().transcript.map((line) => line.text)).toEqual([
      "Previous spoken question", "Previous spoken answer",
    ]));
    expect(useEventStore.getState().activeThreadId).toBe("voice-1");
    expect(useHomeStore.getState().freshVoicePending).toBe(false);
    expect(useHomeStore.getState().voiceSelectionPending).toBe(false);
  });

  test("the new button clears whatever is open", async () => {
    useAgentChatStore.setState({ activeSessionId: "chat-1" });
    useEventStore.setState({ activeKind: "voice", activeThreadId: "voice-1" });
    render(<JarvisHistoryRail />);
    fireEvent.click(await screen.findByTestId("jarvis-history-new"));
    expect(useAgentChatStore.getState().activeSessionId).toBeNull();
    expect(useEventStore.getState().activeThreadId).toBeNull();
  });

  test("new voice resets the backend and opens a blank voice lane without deleting history", async () => {
    useAgentChatStore.setState({ activeSessionId: "chat-1" });
    useEventStore.setState({ activeKind: "voice", activeThreadId: "voice-1", transcription: "unfinished", transcriptionFinal: false });
    useHomeStore.getState().seedTranscript([{ id: "old", who: "user", text: "Previous call", ts: 1 }]);
    render(<JarvisHistoryRail />);
    await screen.findByTestId("jarvis-history-voice-row");
    fireEvent.click(screen.getByTestId("jarvis-history-new-voice"));
    await waitFor(() => expect(setJarvisCardMode).toHaveBeenCalledWith("voice"));
    expect(fetch).toHaveBeenCalledWith("/api/chats/voice/new", { method: "POST" });
    expect(useHomeStore.getState().transcript).toEqual([]);
    expect(useAgentChatStore.getState().activeSessionId).toBeNull();
    expect(useEventStore.getState().activeKind).toBe("voice");
    expect(useEventStore.getState().activeThreadId).toBeNull();
    expect(useEventStore.getState().transcription).toBe("");
    expect(screen.getByTestId("jarvis-history-voice-row").textContent).toContain("Evening call");
  });

  test("a failed voice reset preserves the conversation and offers a retry", async () => {
    useHomeStore.getState().seedTranscript([{ id: "old", who: "user", text: "Previous call", ts: 1 }]);
    render(<JarvisHistoryRail />);
    await screen.findByTestId("jarvis-history-voice-row");
    vi.mocked(fetch).mockResolvedValueOnce(json({}, 500));
    fireEvent.click(screen.getByTestId("jarvis-history-new-voice"));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(useHomeStore.getState().transcript[0].text).toBe("Previous call");
    expect(setJarvisCardMode).not.toHaveBeenCalled();
    expect((screen.getByTestId("jarvis-history-new-voice") as HTMLButtonElement).disabled).toBe(false);
  });
});
