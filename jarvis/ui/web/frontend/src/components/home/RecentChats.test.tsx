import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RecentChats } from "@/components/home/RecentChats";
import { useEventStore, type ConversationSummary } from "@/store/events";
import { useHomeStore } from "@/store/home";

/**
 * The sidebar history lists Jarvis' own conversations — the ones you spoke and
 * the ones you typed — because both are the same assistant. The Agentic IDE's
 * agent chats are listed in the IDE, next to the folder they belong to; a row
 * for one here would open a surface the front page does not have.
 */

function row(kind: ConversationSummary["kind"], id: string, title: string): ConversationSummary {
  return { kind, id, title, preview: title, created_ms: 500, updated_ms: 1_000, message_count: 2 };
}

const CONVERSATIONS = [row("voice", "v1", "Spoken thread"), row("text", "t1", "Typed thread")];

const VOICE_DETAIL = {
  kind: "voice",
  id: "v1",
  title: "Voice session",
  messages: [
    { role: "user", text: "hello", ts_ms: 1_000 },
    { role: "assistant", text: "Hi there.", ts_ms: 2_000 },
  ],
  seeded_turns: 2,
};

const TEXT_DETAIL = {
  kind: "text",
  id: "t1",
  title: "Typed thread",
  messages: [
    { role: "user", text: "was steht an", ts_ms: 1_000 },
    { role: "assistant", text: "Zwei Termine.", ts_ms: 2_000 },
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
      vi.fn(async (url: string) => {
        const href = String(url);
        if (href.endsWith("/resume")) {
          const detail = href.includes("/t1/") ? TEXT_DETAIL : VOICE_DETAIL;
          return new Response(JSON.stringify(detail), { status: 200 });
        }
        // The block polls GET /api/chats on mount; answering with the same
        // rows keeps the refresh from wiping what the test just seeded.
        return new Response(JSON.stringify(CONVERSATIONS), { status: 200 });
      }),
    );
    useEventStore.setState({
      conversations: CONVERSATIONS,
      activeThreadId: null,
      activeKind: "text",
      messages: [],
      activeSection: "board",
    });
    useHomeStore.setState({ surface: "voice", transcript: [] });
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

  it("opens a typed thread on the chat surface, even from the voice stage", async () => {
    render(<RecentChats />);
    fireEvent.click(screen.getByTitle("Typed thread"));
    await flush();
    expect(useHomeStore.getState().surface).toBe("chat");
    expect(useEventStore.getState().activeSection).toBe("chats");
    expect(useEventStore.getState().activeThreadId).toBe("t1");
    expect(useEventStore.getState().activeKind).toBe("text");
    expect(useEventStore.getState().messages.map((m) => m.content)).toEqual([
      "was steht an",
      "Zwei Termine.",
    ]);
    // A typed thread is read on the chat stage, not spoken back into the lane.
    expect(useHomeStore.getState().transcript).toEqual([]);
  });

  it("loads a voice session onto the chat surface when that is where you are", async () => {
    useHomeStore.setState({ surface: "chat" });
    render(<RecentChats />);
    fireEvent.click(screen.getByTitle("Spoken thread"));
    await flush();
    expect(useHomeStore.getState().surface).toBe("chat");
    // Read on the chat stage (components/home/VoiceThreadStage), not seeded
    // into the voice lane.
    expect(useEventStore.getState().activeThreadId).toBe("v1");
    expect(useEventStore.getState().messages.map((m) => m.content)).toEqual(["hello", "Hi there."]);
    expect(useHomeStore.getState().transcript).toEqual([]);
  });

  it("offers the whole archive behind one button", async () => {
    render(<RecentChats />);
    fireEvent.click(screen.getByTestId("see-all-chats"));
    await flush();
    expect(screen.getByTestId("all-chats-dialog")).toBeTruthy();
    // Both kinds are listed there, whatever the sidebar had room for.
    const rows = screen.getAllByTestId("all-chats-row");
    expect(rows.map((r) => r.getAttribute("data-kind")).sort()).toEqual(["text", "voice"]);
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
