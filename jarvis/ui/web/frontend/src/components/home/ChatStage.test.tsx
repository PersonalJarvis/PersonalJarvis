import { act, cleanup, render as rtlRender, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatStage } from "@/components/home/ChatStage";
import type { ThinkingStep } from "@/lib/thinkingSteps";
import { useEventStore, type ChatMessage } from "@/store/events";

// The composer inside the stage names the model that will answer (a query),
// so the stage mounts under a query client like the app.
function render(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function msg(id: string, role: ChatMessage["role"], content: string): ChatMessage {
  return { id, role, content, ts: Number(id.replace(/\D/g, "")) || 0 };
}

function toolStep(id: string, tool: string, status: ThinkingStep["status"] = "done"): ThinkingStep {
  return {
    id,
    kind: "tool",
    labelKey: "thinking.step_tool",
    detail: tool,
    status,
    startedTs: 0,
    durationMs: status === "active" ? undefined : 500,
  };
}

describe("ChatStage", () => {
  let scrollTo: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    // jsdom has no element scrolling; the stage must call the standard API.
    scrollTo = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      writable: true,
      value: scrollTo,
    });
    useEventStore.setState({
      connected: true,
      wsWarming: false,
      messages: [],
      chatThinking: false,
      thinkingSteps: [],
      thinkingStartedTs: null,
      thinkingTraces: {},
      activeThreadId: null,
      assistantName: "Jarvis",
    });
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("shows the empty state with greeting and composer when there is nothing yet", () => {
    render(<ChatStage />);
    expect(screen.getByTestId("chat-stage").getAttribute("data-empty")).toBe("true");
    expect(screen.getByTestId("chat-composer")).toBeTruthy();
  });

  it("renders the live turn with its steps while the assistant is thinking", () => {
    useEventStore.setState({
      messages: [msg("m1", "user", "find my invoices")],
      chatThinking: true,
      thinkingStartedTs: Date.now() - 4000,
      thinkingSteps: [
        { id: "s1", kind: "brain", labelKey: "thinking.step_brain", status: "done", startedTs: 0, durationMs: 700 },
        toolStep("s2", "gmail_search", "active"),
      ],
    });
    render(<ChatStage />);
    const live = screen.getByTestId("chat-turn-live");
    expect(live.textContent).toContain("Jarvis");
    const steps = within(live).getByTestId("turn-steps");
    expect(steps.getAttribute("data-live")).toBe("true");
    expect(steps.getAttribute("data-open")).toBe("true");
    const rows = within(steps).getAllByTestId("turn-step");
    expect(rows).toHaveLength(2);
    expect(rows[1].textContent).toContain("Gmail · search");
    expect(within(rows[1]).getByTestId("turn-step-spinner")).toBeTruthy();
  });

  it("renders a finished trace folded above the answer, tool rows still visible", () => {
    useEventStore.setState({
      messages: [msg("m1", "user", "any github issues?"), msg("m2", "assistant", "Three open issues.")],
      thinkingTraces: {
        m2: {
          durationMs: 6_200,
          steps: [
            { id: "s1", kind: "brain", labelKey: "thinking.step_brain", status: "done", startedTs: 0, durationMs: 900 },
            toolStep("s2", "github_issues"),
          ],
        },
      },
    });
    render(<ChatStage />);
    const answer = screen.getByTestId("chat-message-assistant");
    const steps = within(answer).getByTestId("turn-steps");
    expect(steps.getAttribute("data-open")).toBe("false");
    expect(within(steps).getByTestId("turn-steps-toggle").textContent).toContain("6s");
    const rows = within(steps).getAllByTestId("turn-step");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("GitHub · issues");
    expect(within(rows[0]).getByTestId("turn-step-brand").getAttribute("data-brand-tier")).toBe("logo");
    // The steps sit above the answer text.
    expect(answer.textContent!.indexOf("GitHub · issues")).toBeLessThan(
      answer.textContent!.indexOf("Three open issues."),
    );
  });

  it("scrolls a new user message to the top and keeps a spacer under the turn", () => {
    useEventStore.setState({
      messages: [msg("m1", "user", "hello"), msg("m2", "assistant", "hi")],
    });
    render(<ChatStage />);
    // Opening a conversation: follow the end, once.
    expect(scrollTo).toHaveBeenCalledTimes(1);
    scrollTo.mockClear();

    act(() => {
      useEventStore.getState().pushMessage(msg("m3", "user", "and now?"));
      useEventStore.getState().setChatThinking(true);
    });
    expect(screen.getByTestId("chat-bottom-spacer")).toBeTruthy();
    // The user message is brought to the top (one scroll, smooth or instant).
    expect(scrollTo).toHaveBeenCalledTimes(1);
    const arg = scrollTo.mock.calls[0][0] as { top: number; behavior: string };
    expect(arg.top).toBe(0); // jsdom lays everything out at 0 — the top edge
    expect(["smooth", "auto"]).toContain(arg.behavior);
    scrollTo.mockClear();

    // The answer landing does NOT move the page.
    act(() => {
      useEventStore.getState().finishThinking("m4");
      useEventStore.getState().pushMessage(msg("m4", "assistant", "here you go"));
    });
    expect(scrollTo).not.toHaveBeenCalled();
    expect(screen.queryByTestId("chat-turn-live")).toBeNull();
    expect(screen.getAllByTestId("chat-message-assistant")).toHaveLength(2);
  });

  it("does not render the old thinking card anymore", () => {
    useEventStore.setState({ messages: [msg("m1", "user", "x")], chatThinking: true, thinkingStartedTs: Date.now() });
    render(<ChatStage />);
    // The composer no longer carries a status pill; the live turn does the talking.
    const composer = screen.getByTestId("chat-composer");
    expect(within(composer).queryByRole("status")).toBeNull();
    expect(screen.getByTestId("chat-turn-live")).toBeTruthy();
  });
});
