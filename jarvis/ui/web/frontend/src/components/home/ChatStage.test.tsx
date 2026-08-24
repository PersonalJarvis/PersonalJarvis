import { cleanup, render as rtlRender, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatStage } from "@/components/home/ChatStage";
import { useEventStore } from "@/store/events";

/**
 * The front page's chat is JARVIS with a keyboard — the same assistant the
 * microphone talks to, reading the same store the voice path fills. These
 * tests pin exactly that: the composer that sends on the app socket, the
 * brain's own reasoning steps, its answer streaming in, and a spoken session
 * read in the same column.
 *
 * What must NOT be here is a provider / model / effort / permission picker:
 * those belong to a coding-agent session, which is a different surface
 * (components/agentchat/AgentChatStage). A picker appearing on this stage is
 * the exact regression this file exists to catch.
 */

// The greeting reads the profile name (a query), so the stage mounts under a
// query client like the app.
function render(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const IDLE = {
  connected: true,
  brainProvider: "grok",
  brainModel: "grok-4.3",
  wsWarming: false,
  assistantName: "Jarvis",
  activeKind: "text" as const,
  activeThreadId: null,
  messages: [],
  chatThinking: false,
  thinkingSteps: [],
  thinkingStartedTs: null,
  thinkingTraces: {},
  liveReply: null,
  conversations: [],
};

describe("ChatStage (Jarvis, typed)", () => {
  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      writable: true,
      value: vi.fn(),
    });
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      writable: true,
      value: vi.fn(),
    });
    window.localStorage.clear();
    useEventStore.setState(IDLE);
  });
  afterEach(() => {
    cleanup();
  });

  it("opens on the greeting and Jarvis' own composer — no agent picks", () => {
    render(<ChatStage />);

    expect(screen.getByTestId("chat-stage").getAttribute("data-empty")).toBe("true");
    expect(screen.getByTestId("home-greeting")).toBeTruthy();
    expect(screen.getByTestId("chat-composer")).toBeTruthy();
    // The four coding-session picks have no business on this surface.
    expect(screen.queryByTestId("agent-composer")).toBeNull();
    expect(screen.queryByTestId("composer-provider")).toBeNull();
    expect(screen.queryByTestId("composer-effort")).toBeNull();
    expect(screen.queryByTestId("composer-permission")).toBeNull();
    expect(screen.queryByTestId("composer-plan")).toBeNull();
  });

  it("names the brain that answers, never the realtime voice engine", () => {
    useEventStore.setState({
      activeThreadId: "t1",
      messages: [
        { id: "m1", role: "user", content: "hi", ts: 1 },
        { id: "m2", role: "assistant", content: "Hello.", ts: 2 },
      ],
    });
    render(<ChatStage />);

    // A typed turn runs the classic brain; the realtime engine only answers
    // speech, so its name must not appear on this surface.
    const composer = screen.getByTestId("chat-composer");
    expect(within(composer).getByTestId("composer-model").textContent).toContain("grok-4.3");
    expect(screen.getByTestId("chat-message-assistant").textContent).toContain("grok-4.3");
    expect(document.body.textContent).not.toContain("Live");
  });

  it("hangs the turn off a rail, the way the timeline always did", () => {
    useEventStore.setState({
      activeThreadId: "t1",
      messages: [{ id: "m2", role: "assistant", content: "Done.", ts: 2 }],
    });
    render(<ChatStage />);
    expect(screen.getByTestId("chat-turn-rail")).toBeTruthy();
  });

  it("renders a turn: the person's bubble, then Jarvis' answer as prose", () => {
    useEventStore.setState({
      activeThreadId: "t1",
      messages: [
        { id: "m1", role: "user", content: "What is on today?", ts: 1 },
        {
          id: "m2",
          role: "assistant",
          content: "Two meetings:\n\n- **10:00** standup\n- 14:00 review",
          ts: 2,
        },
      ],
    });
    render(<ChatStage />);

    expect(screen.getByTestId("chat-stage").getAttribute("data-empty")).toBe("false");
    expect(screen.getByTestId("chat-message-user").textContent).toContain("What is on today?");
    const answer = screen.getByTestId("chat-message-assistant");
    expect(answer.textContent).toContain("Jarvis");
    // Markdown is rendered, not printed: the list is a list and the bold is bold.
    expect(within(answer).getAllByRole("listitem")).toHaveLength(2);
    expect(within(answer).getByText("10:00").tagName).toBe("STRONG");
    // The composer stays with the conversation.
    expect(screen.getByTestId("chat-composer")).toBeTruthy();
  });

  it("shows the turn while it runs: the brain's steps and the answer as it is written", () => {
    useEventStore.setState({
      activeThreadId: "t1",
      messages: [{ id: "m1", role: "user", content: "Turn on the lights", ts: 1 }],
      chatThinking: true,
      thinkingStartedTs: Date.now() - 4_000,
      thinkingSteps: [
        {
          id: "s1",
          kind: "tool",
          labelKey: "thinking.step_tool",
          detail: "living room",
          status: "active",
          startedTs: Date.now() - 3_000,
        },
      ],
      liveReply: { text: "On, in the living room", threadId: "t1", done: false, ts: 2 },
    });
    render(<ChatStage />);

    const live = screen.getByTestId("chat-turn-live");
    expect(live.textContent).toContain("living room");
    expect(screen.getByTestId("chat-live-text").textContent).toContain("On, in the living room");
  });

  it("keeps the steps a finished reply came with when it is replayed from the history", () => {
    useEventStore.setState({
      activeThreadId: "t1",
      messages: [
        {
          id: "m2",
          role: "assistant",
          content: "Sent.",
          ts: 2,
          trace: {
            durationMs: 3_200,
            steps: [
              {
                id: "s1",
                kind: "tool",
                labelKey: "thinking.step_tool",
                detail: "mailbox",
                status: "done",
                startedTs: 1,
                durationMs: 900,
              },
            ],
          },
        },
      ],
    });
    render(<ChatStage />);

    expect(screen.getByTestId("chat-message-assistant").textContent).toContain("mailbox");
  });

  it("reads a spoken session in the same column, without a composer", () => {
    useEventStore.setState({
      activeKind: "voice",
      activeThreadId: "v1",
      conversations: [
        {
          kind: "voice",
          id: "v1",
          title: "Kitchen timer",
          preview: "set a timer",
          created_ms: 1,
          updated_ms: 2,
          message_count: 2,
        },
      ],
      messages: [
        { id: "m1", role: "user", content: "set a timer", ts: 1 },
        { id: "m2", role: "assistant", content: "Ten minutes, running.", ts: 2 },
      ],
    });
    render(<ChatStage />);

    const stage = screen.getByTestId("voice-thread-stage");
    expect(stage.getAttribute("data-thread")).toBe("v1");
    expect(stage.textContent).toContain("Ten minutes, running.");
    // A spoken thread is continued by talking.
    expect(screen.queryByTestId("chat-composer")).toBeNull();
    expect(screen.getByTestId("continue-by-voice")).toBeTruthy();
  });
});
