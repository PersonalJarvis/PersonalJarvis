import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ChatsSurface } from "@/views/ChatsSurface";
import { useHomeStore } from "@/store/home";
import { readHomeSurface } from "@/lib/homeSurface";
import { useEventStore } from "@/store/events";
import { useAgentChatStore } from "@/store/agentChat";

/**
 * The front page is ONE chat with a voice mode inside it (2026-10-01). What
 * is under test is the shell: it opens on the chat, voice mode swaps in the
 * spoken stage and hands it the way back to typing, and the mode is remembered. Both
 * stages are stubbed; the composer's voice button is ChatStage's own test.
 */
vi.mock("@/components/home/VoiceStage", () => ({
  VoiceStage: ({ onExit }: { onExit?: () => void }) => (
    <div data-testid="voice">
      <button type="button" data-testid="voice-mode-exit" onClick={onExit}>
        back
      </button>
    </div>
  ),
}));
vi.mock("@/components/home/ChatStage", () => ({
  ChatStage: () => <div data-testid="chat">chat</div>,
}));
vi.mock("@/components/home/HomeAgentChat", () => ({
  default: ({ agentId }: { agentId: string }) => <div data-testid="agent-chat">{agentId}</div>,
}));

const STORAGE_KEY = "jarvis.home.surface.v2";

describe("ChatsSurface (the front page)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useHomeStore.setState({ surface: readHomeSurface(), agentChatId: null, freshVoicePending: false, continuedVoiceId: null, voiceSwitchStopping: false });
    useEventStore.setState({ activeSection: "chats", voiceState: "idle", activeKind: "voice", activeThreadId: null });
  });

  afterEach(cleanup);

  it("gives the page to an agent's chat picked in the sidebar, and back on a new chat", async () => {
    useHomeStore.getState().openAgentChat("agent-7");
    render(<ChatsSurface />);
    expect((await screen.findByTestId("agent-chat")).textContent).toBe("agent-7");
    expect(screen.queryByTestId("chat")).toBeNull();
    const { startNewTextChat } = await import("@/lib/newChat");
    act(() => startNewTextChat());
    expect(await screen.findByTestId("chat")).toBeTruthy();
    expect(useHomeStore.getState().agentChatId).toBeNull();
  });

  it("opens on the chat by default, with nothing above it", async () => {
    render(<ChatsSurface />);
    expect(await screen.findByTestId("chat")).toBeTruthy();
    expect(screen.queryByTestId("voice")).toBeNull();
    expect(screen.queryByTestId("voice-mode-exit")).toBeNull();
    expect(screen.getByTestId("home-view").getAttribute("data-surface")).toBe("chat");
  });

  it("ignores the retired Voice | Chat switch's stored choice", async () => {
    window.localStorage.setItem("jarvis.home.surface.v1", "voice");
    useHomeStore.setState({ surface: readHomeSurface() });
    render(<ChatsSurface />);
    expect(await screen.findByTestId("chat")).toBeTruthy();
  });

  it("stays blank after hangup, late timeline updates, and switching back to typing", async () => {
    const fetcher = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify({ providers: [], sessions: [], session_id: "old-chat" })));
    vi.stubGlobal("fetch", fetcher);
    try {
      useHomeStore.setState({ surface: "voice", continuedVoiceId: "archive" });
      useEventStore.setState({ activeKind: "voice", activeThreadId: "archive" });
      useHomeStore.getState().seedTranscript([{ id: "old", who: "user", text: "Old call", ts: 1 }]);
      render(<ChatsSurface />);
      await act(async () => {
        useHomeStore.getState().ingest("VoiceSessionStarted", { session_id: "call" }, 1);
        useHomeStore.getState().ingest("VoiceSessionEnded", { session_id: "call", hangup_reason: "hotkey" }, 2);
      });
      act(() => useAgentChatStore.setState({ timeline: {
        ...useAgentChatStore.getState().timeline,
        items: [{ type: "user", id: "late", text: "Late stored turn", tsMs: 3, attachments: [] }],
      } }));
      expect(useHomeStore.getState().transcript).toEqual([]);
      expect(useHomeStore.getState().freshVoicePending).toBe(true);
      expect(useEventStore.getState().activeThreadId).toBeNull();
      expect(screen.getByTestId("voice")).toBeTruthy();
      fireEvent.click(screen.getByTestId("voice-mode-exit"));
      expect(await screen.findByTestId("chat")).toBeTruthy();
      expect(useAgentChatStore.getState().activeSessionId).toBeNull();
      expect(fetcher.mock.calls.some(([url, init]) => String(url) === "/api/agent-chat/voice-chat" && !init?.method)).toBe(false);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("goes back to typing from voice mode and remembers it", async () => {
    window.localStorage.setItem(STORAGE_KEY, "voice");
    useHomeStore.setState({ surface: readHomeSurface() });
    render(<ChatsSurface />);
    expect(screen.getByTestId("voice")).toBeTruthy();
    fireEvent.click(screen.getByTestId("voice-mode-exit"));
    expect(await screen.findByTestId("chat")).toBeTruthy();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("chat");
  });

  it("lands on the chat when the stored value is corrupt", async () => {
    window.localStorage.setItem(STORAGE_KEY, "garbage");
    useHomeStore.setState({ surface: readHomeSurface() });
    render(<ChatsSurface />);
    expect(await screen.findByTestId("chat")).toBeTruthy();
  });
});
