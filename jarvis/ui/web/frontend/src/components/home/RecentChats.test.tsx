import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RecentChats } from "@/components/home/RecentChats";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore, type ConversationSummary } from "@/store/events";
import { useHomeStore } from "@/store/home";

function row(kind: ConversationSummary["kind"], id: string, title: string): ConversationSummary {
  return { kind, id, title, preview: title, created_ms: 500, updated_ms: 1_000, message_count: 2 };
}

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
          : new Response(JSON.stringify([]), { status: 200 }),
      ),
    );
    useEventStore.setState({
      conversations: [row("voice", "v1", "Spoken thread"), row("text", "t1", "Typed thread")],
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

  it("opens a voice session on the chat surface when that is where you are", async () => {
    useHomeStore.setState({ surface: "chat" });
    render(<RecentChats />);
    fireEvent.click(screen.getByTitle("Spoken thread"));
    await flush();
    expect(useHomeStore.getState().surface).toBe("chat");
    expect(useHomeStore.getState().transcript).toEqual([]);
  });
});
