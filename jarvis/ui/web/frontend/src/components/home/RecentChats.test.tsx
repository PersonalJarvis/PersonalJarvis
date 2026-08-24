import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RecentChats } from "@/components/home/RecentChats";
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
    });
    useHomeStore.setState({ surface: "voice", transcript: [] });
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
