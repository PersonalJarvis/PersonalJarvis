import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RecentChats, compactChatTitle } from "@/components/home/RecentChats";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore, type ConversationSummary } from "@/store/events";
import { useHomeStore } from "@/store/home";

function row(kind: ConversationSummary["kind"], id: string, title: string): ConversationSummary {
  return { kind, id, title, preview: title, created_ms: 500, updated_ms: 1_000, message_count: 2 };
}

const CONVERSATIONS = [row("voice", "v1", "Spoken thread"), row("text", "t1", "Typed thread")];

const DETAIL = {
  kind: "voice",
  id: "v1",
  title: "Voice session",
  messages: [
    { role: "user", text: "hello", ts_ms: 1_000 },
    { role: "assistant", text: "Hi there.", ts_ms: 2_000 },
  ],
  seeded_turns: 2,
};

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("RecentChats", () => {
  beforeEach(() => {
    localStorage.removeItem("jarvis.sidebar.pinned-chats.v1");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        String(url).endsWith("/resume")
          ? new Response(JSON.stringify(DETAIL), { status: 200 })
          : // The block polls GET /api/chats on mount; answering with the same
            // rows keeps the refresh from wiping what the test just seeded.
            new Response(JSON.stringify(CONVERSATIONS), { status: 200 }),
      ),
    );
    useEventStore.setState({
      conversations: CONVERSATIONS,
      activeThreadId: null,
      messages: [],
      activeSection: "board",
      activeKind: "text",
      voiceState: "idle",
    });
    useHomeStore.setState({ surface: "voice", transcript: [], liveReply: "", liveSessionId: null,
      continuedVoiceId: null, liveConversationId: null, voiceSelectionPending: false, voiceSwitchStopping: false });
    useAgentChatStore.setState({
      sessions: [
        {
          session_id: "s1",
          title: "Agent chat",
          provider: "claude-api",
          model: "",
          effort: "high",
          cwd: "C:\work",
          permission_mode: "acceptEdits",
          surface: "jarvis",
          vendor_session: null,
          created_ms: 500,
          updated_ms: 900,
          message_count: 2,
          preview: "Agent chat",
        },
      ],
      activeSessionId: null,
      loadSessions: async () => {},
      newChat: vi.fn(),
      openSession: vi.fn((id: string) => useAgentChatStore.setState({ activeSessionId: id })),
    });
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("pins a chat without duplicating it and remembers the pin", async () => {
    const view = render(<RecentChats />);
    fireEvent.click(screen.getByRole("button", { name: "Pin chat: Agent chat" }));
    expect(screen.getByTestId("pinned-chats").textContent).toContain("Agent chat");
    expect(screen.getAllByTitle("Agent chat")).toHaveLength(1);
    view.unmount();
    render(<RecentChats />);
    expect(screen.getByTestId("pinned-chats").textContent).toContain("Agent chat");
    fireEvent.click(screen.getByRole("button", { name: "Unpin chat: Agent chat" }));
    expect(screen.queryByTestId("pinned-chats")).toBeNull();
    expect(screen.getAllByTitle("Agent chat")).toHaveLength(1);
    await flush();
  });

  it("keeps a voice session on the voice surface and loads its words into the lane", async () => {
    render(<RecentChats />);
    fireEvent.click(screen.getByTitle("Spoken thread"));
    await flush();
    expect(useHomeStore.getState().surface).toBe("voice");
    expect(useEventStore.getState().activeSection).toBe("chats");
    expect(useEventStore.getState().activeThreadId).toBe("v1");
    expect(useHomeStore.getState().transcript.map((l) => [l.who, l.text])).toEqual([
      ["user", "hello"],
      ["assistant", "Hi there."],
    ]);
  });

  it.each(["listening", "thinking", "speaking", "paused", "connecting"] as const)(
    "returns to the current %s call without ending it or replacing its live transcript", async (voiceState) => {
      const transcript = [{ id: "live-line", who: "user" as const, text: "Current question", ts: 3_000 }];
      useEventStore.setState({ voiceState, activeSection: "board" });
      useHomeStore.setState({ liveSessionId: "v1", transcript, liveReply: "Reply in progress", surface: "chat" });
      render(<RecentChats />);
      fireEvent.click(screen.getByTitle("Spoken thread"));
      await flush();
      expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/hangup") || String(url).endsWith("/resume"))).toBe(false);
      expect(useEventStore.getState().voiceState).toBe(voiceState);
      expect(useEventStore.getState().activeSection).toBe("chats");
      expect(useEventStore.getState().activeThreadId).toBe("v1");
      expect(useHomeStore.getState().surface).toBe("voice");
      expect(useHomeStore.getState().transcript).toBe(transcript);
      expect(useHomeStore.getState().liveReply).toBe("Reply in progress");
      expect(useHomeStore.getState().voiceSelectionPending).toBe(false);
      expect(useAgentChatStore.getState().newChat).not.toHaveBeenCalled();
    },
  );

  it("recognizes a running call recorded into a continued voice chat", async () => {
    useEventStore.setState({ voiceState: "speaking", activeKind: "voice", activeThreadId: "v1" });
    useHomeStore.setState({ continuedVoiceId: "v1", liveReply: "Still speaking" });
    useHomeStore.getState().ingest("VoiceSessionStarted", { session_id: "current-call" }, 3);
    useEventStore.setState({ activeKind: "text", activeThreadId: null });
    render(<RecentChats />);
    fireEvent.click(screen.getByTitle("Spoken thread"));
    await flush();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/hangup") || String(url).endsWith("/resume"))).toBe(false);
    expect(useHomeStore.getState().liveSessionId).toBe("current-call");
    expect(useHomeStore.getState().continuedVoiceId).toBe("v1");
    expect(useHomeStore.getState().liveReply).toBe("Still speaking");
  });

  it("still loads an ended call when its last session id remains in the store", async () => {
    useHomeStore.setState({ liveSessionId: "v1", continuedVoiceId: "v1" });
    render(<RecentChats />);
    fireEvent.click(screen.getByTitle("Spoken thread"));
    await flush();
    expect(fetch).toHaveBeenCalledWith("/api/chats/voice/v1/resume", { method: "POST" });
    expect(useHomeStore.getState().transcript.map((line) => line.text)).toEqual(["hello", "Hi there."]);
  });

  it.each(["other-archive", "v1"])("does not mistake archive selection %s for a fresh running call", async (continuedVoiceId) => {
    useEventStore.setState({ voiceState: "listening" });
    useHomeStore.setState({ continuedVoiceId });
    useHomeStore.getState().ingest("VoiceSessionStarted", { session_id: "other-call" }, 2);
    render(<RecentChats />);
    fireEvent.click(screen.getByTitle("Spoken thread"));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/voice/hangup", { method: "POST", cache: "no-store" }));
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith("/resume"))).toBe(false);
    await act(async () => {
      useHomeStore.getState().ingest("VoiceSessionEnded", { session_id: "other-call", hangup_reason: "client_stop" }, 3);
      useEventStore.getState().setVoice("idle");
    });
    await waitFor(() => expect(useHomeStore.getState().transcript.map((line) => line.text)).toEqual(["hello", "Hi there."]));
  });

  it("keeps the live transcript when an older archive read finishes after returning to the call", async () => {
    let finish!: (response: Response) => void;
    vi.mocked(fetch).mockImplementation(async (url) => String(url).endsWith("/resume")
      ? new Promise<Response>((resolve) => { finish = resolve; })
      : new Response(JSON.stringify(CONVERSATIONS), { status: 200 }));
    render(<RecentChats />);
    fireEvent.click(screen.getByTitle("Spoken thread"));
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    const transcript = [{ id: "live", who: "user" as const, text: "New live words", ts: 3 }];
    act(() => {
      useHomeStore.setState({ liveSessionId: "v1", transcript });
      useEventStore.setState({ voiceState: "listening", activeSection: "board" });
    });
    fireEvent.click(screen.getByTitle("Spoken thread"));
    await act(async () => { finish(new Response(JSON.stringify(DETAIL), { status: 200 })); });
    expect(useHomeStore.getState().transcript).toBe(transcript);
    expect(useEventStore.getState().activeThreadId).toBe("v1");
    expect(useHomeStore.getState().voiceSelectionPending).toBe(false);
  });

  it("marks voice and typed chats apart and names a topicless voice chat by its kind", async () => {
    useEventStore.setState({
      conversations: [...CONVERSATIONS, { ...row("voice", "v2", ""), preview: "Hallo" }],
    });
    render(<RecentChats />);
    // Every row carries exactly one kind mark: sound bars for voice, a ring for typed.
    for (const item of screen.getAllByTestId("recent-chat-row")) {
      const marks = item.querySelectorAll("[data-kind-mark]");
      expect(marks).toHaveLength(1);
      expect(marks[0].getAttribute("data-kind-mark")).toBe(item.getAttribute("data-kind"));
      expect(item.querySelectorAll("svg")).toHaveLength(item.getAttribute("data-kind") === "voice" ? 1 : 0);
    }
    const voice = screen.getByRole("button", { name: "Voice: Spoken thread" });
    expect(voice.getAttribute("data-kind")).toBe("voice");
    // A voice chat with no topic never shows its first words as a headline.
    expect(screen.queryByText("Hallo")).toBeNull();
    const untitled = screen.getAllByTestId("recent-chat-row").find(
      (item) => item.textContent?.startsWith("Voice chat"),
    );
    expect(untitled?.querySelector(".text-muted-foreground")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Pin chat: Spoken thread" }));
    expect(screen.getByTestId("pinned-chats").contains(
      screen.getByRole("button", { name: "Voice: Spoken thread" }),
    )).toBe(true);
    expect(screen.getAllByTitle("Spoken thread")).toHaveLength(1);
    await flush();
  });

  it("opens an agent chat on the chat surface even from the voice stage", async () => {
    render(<RecentChats />);
    // The classic brain's text threads are no longer listed — the chat
    // surface is the agent chat now (components/home/ChatStage).
    expect(screen.queryByTitle("Typed thread")).toBeNull();
    fireEvent.click(screen.getByTitle("Agent chat"));
    await flush();
    expect(useHomeStore.getState().surface).toBe("chat");
    expect(useEventStore.getState().activeSection).toBe("chats");
    expect(useAgentChatStore.getState().openSession).toHaveBeenCalledWith("s1");
    expect(useHomeStore.getState().transcript).toEqual([]);
  });

  it("clears the voice thread when an agent chat takes the stage", async () => {
    useEventStore.setState({ activeKind: "voice", activeThreadId: "v1" });
    render(<RecentChats />);
    fireEvent.click(screen.getByTitle("Agent chat"));
    await flush();
    // Both conversations claiming the stage is what made a click look like it
    // did nothing: the chat stage kept showing the previous one.
    expect(useEventStore.getState().activeThreadId).toBeNull();
    expect(useEventStore.getState().messages).toEqual([]);
  });

  it("loads a voice session onto the chat surface when that is where you are", async () => {
    useHomeStore.setState({ surface: "chat" });
    render(<RecentChats />);
    fireEvent.click(screen.getByTitle("Spoken thread"));
    await flush();
    expect(useHomeStore.getState().surface).toBe("chat");
    // Read on the chat stage (components/home/VoiceThreadStage), not seeded
    // into the voice lane — and the agent session is ended so it cannot keep
    // the stage.
    expect(useEventStore.getState().activeThreadId).toBe("v1");
    expect(useEventStore.getState().messages.map((m) => m.content)).toEqual(["hello", "Hi there."]);
    expect(useAgentChatStore.getState().newChat).toHaveBeenCalled();
    expect(useHomeStore.getState().transcript).toEqual([]);
  });

  it("offers the whole archive behind one button", async () => {
    render(<RecentChats />);
    fireEvent.click(screen.getByTestId("see-all-chats"));
    await flush();
    expect(screen.getByTestId("all-chats-dialog")).toBeTruthy();
    // Both kinds are listed there, whatever the sidebar had room for.
    const rows = screen.getAllByTestId("all-chats-row");
    expect(rows.map((r) => r.getAttribute("data-kind")).sort()).toEqual(["agent", "voice"]);
  });

  it("filters the archive by what you type", async () => {
    render(<RecentChats />);
    fireEvent.click(screen.getByTestId("see-all-chats"));
    await flush();
    fireEvent.change(screen.getByTestId("all-chats-search"), { target: { value: "spoken" } });
    const rows = screen.getAllByTestId("all-chats-row");
    expect(rows).toHaveLength(1);
    expect(rows[0].getAttribute("data-kind")).toBe("voice");
  });
});

describe("compact chat labels", () => {
  it("keeps the whole title so a wider sidebar can show more of it", () => {
    expect(compactChatTitle("Review agent routines")).toBe("Review agent routines");
    const title = "Verify the GitHub marketplace plugin with one read-only request and report the results";
    expect(compactChatTitle(`  ${title}  `)).toBe(title);
  });
});
