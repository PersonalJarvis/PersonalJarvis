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

const DB = {
  question: "Which database should the project use?",
  options: [
    { label: "SQLite", description: "Zero setup" },
    { label: "Postgres", description: "Scales further" },
  ],
  recommended: 0,
  recommendation_reason: "No server to run.",
};
const HOST = {
  question: "Where should it run?",
  options: [{ label: "Laptop" }, { label: "VPS" }, { label: "Both" }],
  recommended: 0,
  recommendation_reason: "Nothing to pay for.",
};

function asking(extra: AgentChatEvent[] = [], questions = [DB, HOST]): AgentChatEvent[] {
  return [
    ev("turn_started", { turn_id: "t1" }),
    ev("tool_call", { turn_id: "t1", call_id: "c1", name: "mcp__jarvis__society_ask_user", input: {} }),
    ev("question_required", {
      turn_id: "t1",
      question_id: "q1",
      asker: "Ada",
      questions,
      expires_ms: Date.now() + 5 * 60_000,
    }),
    ...extra,
  ];
}

const ANSWERED_FIRST = ev("question_progress", {
  turn_id: "t1",
  question_id: "q1",
  answers: [{ answer: "Postgres", option_index: 1, source: "person" }, null],
  expires_ms: Date.now() + 5 * 60_000,
});

afterEach(() => {
  cleanup();
  seq = 0;
  vi.restoreAllMocks();
});

describe("question events", () => {
  it("attach the series to the ask call and record each answer", () => {
    const tl = reduceEvents(EMPTY_TIMELINE, asking([
      ANSWERED_FIRST,
      ev("question_resolved", {
        turn_id: "t1",
        question_id: "q1",
        answers: [
          { answer: "Postgres", option_index: 1, source: "person" },
          { answer: "Laptop", option_index: 0, source: "timeout" },
        ],
      }),
    ]));
    const turn = tl.items[0] as TurnItem;
    expect(turn.blocks).toHaveLength(1);
    const block = turn.blocks[0] as ToolBlock;
    expect(block.callId).toBe("c1");
    expect(block.question?.questions.map((q) => q.question)).toEqual([DB.question, HOST.question]);
    expect(block.question?.answers.map((a) => a?.source)).toEqual(["person", "timeout"]);
    expect(block.question?.closed).toBe(true);
  });

  it("read an older single-question event", () => {
    const tl = reduceEvents(EMPTY_TIMELINE, [
      ev("turn_started", { turn_id: "t1" }),
      ev("question_required", { turn_id: "t1", question_id: "q0", ...DB }),
      ev("question_resolved", { turn_id: "t1", question_id: "q0", answer: "SQLite", option_index: 0, source: "timeout" }),
    ]);
    const block = (tl.items[0] as TurnItem).blocks[0] as ToolBlock;
    expect(block.question?.questions).toHaveLength(1);
    expect(block.question?.answers[0]).toEqual({ text: "SQLite", optionIndex: 0, source: "timeout" });
  });

  it("close an unanswered card when the turn ends", () => {
    const tl = reduceEvents(EMPTY_TIMELINE, asking([ev("turn_finished", { turn_id: "t1", status: "cancelled" })]));
    const block = (tl.items[0] as TurnItem).blocks[0] as ToolBlock;
    expect(block.question?.closed).toBe(true);
    expect(block.question?.answers.map((a) => a?.source)).toEqual(["closed", "closed"]);
  });
});

describe("QuestionCard", () => {
  function draw(events: AgentChatEvent[]) {
    const store = createAgentChatStore("jarvis");
    const answer = vi.fn(async () => undefined);
    const skip = vi.fn(async () => undefined);
    store.setState({ answerQuestion: answer, skipQuestion: skip });
    render(
      <AgentChatStoreProvider store={store}>
        <AgentTimeline items={reduceEvents(EMPTY_TIMELINE, events).items} assistantName="Ada" providerLabel={(id) => id} onDecide={() => undefined} />
      </AgentChatStoreProvider>,
    );
    return { answer, skip };
  }

  it("titles the card with the agent, steps through the series and lists lettered answers", async () => {
    const { answer } = draw(asking());
    const card = screen.getByTestId("question-card");
    expect(card.getAttribute("data-state")).toBe("open");
    expect(card.textContent).toContain("Ada");
    expect(card.textContent).toContain(DB.question);
    expect(screen.getByTestId("question-step").textContent).toMatch(/1\D+2/);
    const options = card.querySelectorAll("button[data-recommended]");
    expect(options).toHaveLength(2);
    expect(options[0].textContent).toMatch(/^A/);
    expect(options[0].getAttribute("data-recommended")).toBe("true");
    expect(card.textContent).toContain("No server to run.");
    expect(screen.getByTestId("question-countdown").textContent).toMatch(/[45]:\d\d/);
    fireEvent.click(options[1]);
    await waitFor(() => expect(answer).toHaveBeenCalledWith("q1", 0, { optionIndex: 1 }));
  });

  it("moves to the next question once the first is answered", () => {
    const { answer } = draw(asking([ANSWERED_FIRST]));
    const card = screen.getByTestId("question-card");
    expect(card.textContent).toContain(HOST.question);
    expect(screen.getByTestId("question-step").textContent).toMatch(/2\D+2/);
    fireEvent.keyDown(card, { key: "b" });
    expect(answer).toHaveBeenCalledWith("q1", 1, { optionIndex: 1 });
  });

  it("draws no row for the calls that only wait on the card", () => {
    draw(asking([
      ev("tool_result", { turn_id: "t1", call_id: "c1", output: "waiting" }),
      ev("tool_call", { turn_id: "t1", call_id: "c2", name: "mcp__jarvis__society_ask_user", input: { wait_for: "q1" } }),
      ev("tool_result", { turn_id: "t1", call_id: "c2", output: "waiting" }),
    ]));
    expect(screen.getAllByTestId("question-card")).toHaveLength(1);
    expect(document.querySelector("[data-trace-tool]")).toBeNull();
  });

  it("sends a typed answer", async () => {
    const { answer } = draw(asking([], [DB]));
    expect(screen.queryByTestId("question-step")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /own answer|eigene antwort/i }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "MySQL" } });
    fireEvent.submit(screen.getByRole("textbox").closest("form")!);
    await waitFor(() => expect(answer).toHaveBeenCalledWith("q1", 0, { text: "MySQL" }));
  });

  it("closing the card leaves the questions to the agent", async () => {
    const { skip } = draw(asking());
    fireEvent.click(screen.getByRole("button", { name: /let the agent decide|agenten entscheiden/i }));
    await waitFor(() => expect(skip).toHaveBeenCalledWith("q1"));
  });

  it("shows a finished series as answers", () => {
    draw(asking([
      ev("question_resolved", {
        turn_id: "t1",
        question_id: "q1",
        answers: [
          { answer: "Postgres", option_index: 1, source: "person" },
          { answer: "Laptop", option_index: 0, source: "skipped" },
        ],
      }),
    ]));
    const card = screen.getByTestId("question-card");
    expect(card.getAttribute("data-state")).toBe("person skipped");
    expect(card.textContent).toContain("Postgres");
    expect(card.textContent).toContain("Laptop");
    expect(card.querySelector("button")).toBeNull();
  });
});
