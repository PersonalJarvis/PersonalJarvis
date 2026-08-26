/**
 * The chat stage: a terminal's transcript, drawn with the agent chat's timeline.
 *
 * Pinned here: the poll's cadence follows the pane's state, an unchanged poll
 * re-renders nothing, the transcript's events come out as the same turn the
 * front page's chat would draw (thinking, tool call with result, answer, the
 * closing line), the composer types what was written into the pane verbatim,
 * and a CLI without a readable record says so instead of showing a blank.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "@/lib/agenticIdeApi";
import type { TerminalTimelineResponse } from "@/lib/agenticIdeApi";
import type { AgentChatEvent } from "@/lib/agentChatApi";
import {
  eventsSignature,
  PaneChat,
  POLL_IDLE_MS,
  POLL_WORKING_MS,
  pollIntervalFor,
} from "@/components/agentic/PaneChat";

vi.mock("@/lib/agenticIdeApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/agenticIdeApi")>()),
  fetchTerminalTimeline: vi.fn(),
  promptTerminal: vi.fn(),
}));

const T0 = 1_787_800_000_000;

function ev(seq: number, kind: string, payload: Record<string, unknown>, at = 0): AgentChatEvent {
  return { seq, ts_ms: T0 + at, kind, payload };
}

/** One finished exchange, the way `agent_transcript.read_events` writes it. */
const EXCHANGE: AgentChatEvent[] = [
  ev(1, "user_message", { text: "Fix the login bug" }),
  ev(2, "turn_started", {
    turn_id: "turn-1",
    provider: "claude",
    model: "claude-opus-5",
    effort: "xhigh",
    runner: "cli",
  }, 1000),
  ev(3, "reasoning", { turn_id: "turn-1", message_id: "m1", text: "", duration_ms: 3500 }, 1000),
  ev(4, "assistant_text", { turn_id: "turn-1", message_id: "r1", text: "Looking at it now." }, 4500),
  ev(5, "tool_call", {
    turn_id: "turn-1",
    call_id: "call-1",
    name: "Read",
    input: { file_path: "src/login.ts" },
    summary: "src/login.ts",
  }, 5000),
  ev(6, "tool_result", {
    turn_id: "turn-1",
    call_id: "call-1",
    output: "export const login = 1;",
    is_error: false,
    duration_ms: 2000,
  }, 7000),
  ev(7, "assistant_text", { turn_id: "turn-1", message_id: "r2", text: "Found it." }, 9000),
  ev(8, "turn_finished", {
    turn_id: "turn-1",
    status: "done",
    duration_ms: 8000,
    usage: { output_tokens: 80 },
    error: null,
    cost_usd: null,
  }, 9000),
];

function answer(overrides: Partial<TerminalTimelineResponse> = {}): TerminalTimelineResponse {
  return {
    terminal: "T1",
    agent: "claude",
    readable: true,
    available: true,
    live: false,
    activity: "",
    events: EXCHANGE,
    ...overrides,
  };
}

function renderStage(props: Partial<React.ComponentProps<typeof PaneChat>> = {}) {
  const onShowTerminal = vi.fn();
  const utils = render(
    <PaneChat
      terminal="T1"
      workspaceId="ide_test"
      agentLabel="Claude Code"
      activity=""
      promptable
      onShowTerminal={onShowTerminal}
      {...props}
    />,
  );
  return { ...utils, onShowTerminal };
}

beforeEach(() => {
  vi.mocked(api.fetchTerminalTimeline).mockReset();
  vi.mocked(api.promptTerminal).mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("pollIntervalFor", () => {
  it("reads quickly while the agent works and slowly once it stops", () => {
    expect(pollIntervalFor(true, "")).toBe(POLL_WORKING_MS);
    expect(pollIntervalFor(false, "working")).toBe(POLL_WORKING_MS);
    expect(pollIntervalFor(false, "starting")).toBe(POLL_WORKING_MS);
    expect(pollIntervalFor(false, "waiting")).toBe(POLL_IDLE_MS);
    expect(pollIntervalFor(false, "")).toBe(POLL_IDLE_MS);
  });
});

describe("eventsSignature", () => {
  it("tells a grown transcript from an unchanged one", () => {
    const same = eventsSignature(EXCHANGE, false);
    expect(eventsSignature([...EXCHANGE], false)).toBe(same);
    expect(eventsSignature(EXCHANGE.slice(0, -1), false)).not.toBe(same);
    expect(eventsSignature(EXCHANGE, true)).not.toBe(same);
    // A record that grew in place — the same seq, more text — is a change.
    const grown = EXCHANGE.map((e) =>
      e.seq === 7 ? { ...e, payload: { ...e.payload, text: "Found it, and fixed it." } } : e,
    );
    expect(eventsSignature(grown, false)).not.toBe(same);
  });
});

describe("PaneChat", () => {
  it("draws the transcript as the chat's own turn", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    renderStage();

    await screen.findByTestId("pane-chat-timeline");
    expect(api.fetchTerminalTimeline).toHaveBeenCalledWith("T1", "ide_test");
    // The person's line, and ONE assistant turn under the pane's byline.
    expect(screen.getByTestId("agent-message-user").textContent).toContain("Fix the login bug");
    const turns = screen.getAllByTestId("agent-turn");
    expect(turns).toHaveLength(1);
    expect(turns[0].getAttribute("data-status")).toBe("done");
    expect(turns[0].textContent).toContain("Claude Code");
    expect(turns[0].textContent).toContain("claude-opus-5");
    expect(turns[0].textContent).toContain("Looking at it now.");
    expect(turns[0].textContent).toContain("Found it.");
    // The tool call is a row under the name the CLI's own log uses.
    expect(turns[0].textContent).toContain("Read");
    expect(turns[0].textContent).toContain("src/login.ts");
    // The closing line says the turn ended, with its output tokens.
    expect(screen.getByTestId("agent-turn-footer").getAttribute("data-outcome")).toBe("done");
  });

  it("says when the CLI keeps no readable record, with the way to the terminal", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(
      answer({ readable: false, available: false, events: [] }),
    );
    const { onShowTerminal } = renderStage({ agentLabel: "Grok Build" });

    const empty = await screen.findByTestId("pane-chat-not-readable");
    expect(empty.textContent).toContain("No readable record");
    // No composer either: there is no conversation to add to.
    expect(screen.queryByTestId("pane-chat-composer")).toBeNull();
    fireEvent.click(screen.getByTestId("pane-chat-show-terminal"));
    expect(onShowTerminal).toHaveBeenCalledTimes(1);
  });

  it("waits, rather than gives up, while the record has not appeared yet", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(
      answer({ available: false, events: [] }),
    );
    renderStage();
    await screen.findByTestId("pane-chat-waiting");
    expect(screen.getByTestId("pane-chat-composer")).toBeTruthy();
  });

  it("flags a pane that is asking something only the terminal can answer", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    renderStage({ activity: "asking" });
    await screen.findByTestId("pane-chat-timeline");
    expect(screen.getByTestId("pane-chat-asking").textContent).toContain(
      "The agent is asking you something",
    );
  });

  it("types what was written into the pane, verbatim, and reads again", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    vi.mocked(api.promptTerminal).mockResolvedValue({
      terminal: "T1",
      sent: "Now the tests",
      composed_by: "raw",
      files: [],
      submitted: true,
    });
    renderStage();
    await screen.findByTestId("pane-chat-timeline");
    const before = vi.mocked(api.fetchTerminalTimeline).mock.calls.length;

    const box = screen.getByPlaceholderText(/Message T1/) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "Now the tests" } });
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() =>
      expect(api.promptTerminal).toHaveBeenCalledWith("T1", "Now the tests", { compose: false }),
    );
    await waitFor(() => expect(box.value).toBe(""));
    // A send re-reads the record at once instead of waiting for the next poll.
    await waitFor(() =>
      expect(vi.mocked(api.fetchTerminalTimeline).mock.calls.length).toBeGreaterThan(before),
    );
  });

  it("says so when the pane did not take the message", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    vi.mocked(api.promptTerminal).mockResolvedValue({
      terminal: "T1",
      sent: "hi",
      composed_by: "raw",
      files: [],
      submitted: false,
      detail: "still in the input box",
    });
    renderStage();
    await screen.findByTestId("pane-chat-timeline");
    const box = screen.getByPlaceholderText(/Message T1/);
    fireEvent.change(box, { target: { value: "hi" } });
    fireEvent.click(screen.getByTestId("pane-chat-send"));
    const notice = await screen.findByTestId("pane-chat-notice-warning");
    expect(notice.textContent).toContain("T1 did not take the message: still in the input box");
  });

  it("keeps the column on one failed poll and re-polls on the working cadence", async () => {
    vi.useFakeTimers();
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValueOnce(answer({ live: true }));
    renderStage();
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByTestId("pane-chat-timeline")).toBeTruthy();

    vi.mocked(api.fetchTerminalTimeline).mockRejectedValueOnce(new Error("offline"));
    await act(async () => {
      vi.advanceTimersByTime(POLL_WORKING_MS);
      await Promise.resolve();
    });
    // Still there: a column that empties itself on one failed read would say
    // the conversation is gone.
    expect(screen.getByTestId("pane-chat-timeline")).toBeTruthy();
    expect(api.fetchTerminalTimeline).toHaveBeenCalledTimes(2);
  });
});
