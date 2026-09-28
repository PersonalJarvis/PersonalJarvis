import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentChatEvent } from "@/lib/agentChatApi";
import { createAgentChatStore } from "@/store/agentChat";
import { AgentChatStoreProvider } from "./AgentChatStoreContext";
import { AgentTimeline } from "./AgentTimeline";
import { EMPTY_TIMELINE, reduceEvents, type ToolBlock, type TurnItem } from "./reduce";

let seq = 0;
function ev(kind: string, payload: Record<string, unknown>, tsMs = 1000): AgentChatEvent {
  return { seq: ++seq, ts_ms: tsMs, kind, payload } as AgentChatEvent;
}

const ASKED = {
  turn_id: "t1",
  question_id: "q1",
  question: "Which database should the project use?",
  header: "Database",
  options: [
    { label: "SQLite", description: "Zero setup" },
    { label: "Postgres", description: "Scales further" },
  ],
  recommended: 0,
  recommendation_reason: "No server to run.",
  expires_ms: Date.now() + 5 * 60_000,
};

function asking(extra: AgentChatEvent[] = []): AgentChatEvent[] {
  return [
    ev("turn_started", { turn_id: "t1" }),
    ev("tool_call", { turn_id: "t1", call_id: "c1", name: "mcp__jarvis__society_ask_user", input: {} }),
    ev("question_required", ASKED),
    ...extra,
  ];
}

afterEach(() => {
  cleanup();
  seq = 0;
  vi.restoreAllMocks();
});

describe("question events", () => {
  it("attach the card to the ask call and record the answer", () => {
    const tl = reduceEvents(EMPTY_TIMELINE, asking([
      ev("question_resolved", { turn_id: "t1", question_id: "q1", answer: "SQLite", option_index: 0, source: "timeout", auto: true }),
    ]));
    const turn = tl.items[0] as TurnItem;
    expect(turn.blocks).toHaveLength(1);
    const block = turn.blocks[0] as ToolBlock;
    expect(block.callId).toBe("c1");
    expect(block.question?.options.map((o) => o.label)).toEqual(["SQLite", "Postgres"]);
    expect(block.question?.answer).toEqual({ text: "SQLite", optionIndex: 0, source: "timeout" });
  });

  it("close an unanswered card when the turn ends", () => {
    const tl = reduceEvents(EMPTY_TIMELINE, asking([ev("turn_finished", { turn_id: "t1", status: "cancelled" })]));
    const block = (tl.items[0] as TurnItem).blocks[0] as ToolBlock;
    expect(block.question?.answer?.source).toBe("closed");
  });
});

describe("QuestionCard", () => {
  function draw(events: AgentChatEvent[]) {
    const store = createAgentChatStore("jarvis");
    const answer = vi.fn(async () => undefined);
    store.setState({ answerQuestion: answer });
    render(
      <AgentChatStoreProvider store={store}>
        <AgentTimeline items={reduceEvents(EMPTY_TIMELINE, events).items} assistantName="Ada" providerLabel={(id) => id} onDecide={() => undefined} />
      </AgentChatStoreProvider>,
    );
    return answer;
  }

  it("marks the recommendation first, counts down and sends a pick", async () => {
    const answer = draw(asking());
    const card = screen.getByTestId("question-card");
    expect(card.getAttribute("data-state")).toBe("open");
    const options = card.querySelectorAll("button[data-recommended]");
    expect(options[0].getAttribute("data-recommended")).toBe("true");
    expect(options[0].textContent).toContain("SQLite");
    expect(options[0].textContent).toContain("No server to run.");
    expect(screen.getByTestId("question-countdown").textContent).toMatch(/[45]:\d\d/);
    fireEvent.click(options[1]);
    await waitFor(() => expect(answer).toHaveBeenCalledWith("q1", { optionIndex: 1 }));
  });

  it("sends a typed answer", async () => {
    const answer = draw(asking());
    fireEvent.click(screen.getByRole("button", { name: /different answer|andere antwort/i }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "MySQL" } });
    fireEvent.submit(screen.getByRole("textbox").closest("form")!);
    await waitFor(() => expect(answer).toHaveBeenCalledWith("q1", { text: "MySQL" }));
  });

  it("shows an automatic pick as answered", () => {
    draw(asking([
      ev("question_resolved", { turn_id: "t1", question_id: "q1", answer: "SQLite", option_index: 0, source: "timeout", auto: true }),
    ]));
    const card = screen.getByTestId("question-card");
    expect(card.getAttribute("data-state")).toBe("timeout");
    expect(card.textContent).toContain("SQLite");
    expect(card.querySelector("button")).toBeNull();
  });
});
