/**
 * Regression guard for the "Jarvis repeated his answer twice" bug
 * (typed-chat, word-for-word identical duplicate).
 *
 * Root cause: the WebSocket transport is at-least-once — reconnect replays,
 * connection-churn double-forwards (page reload via main.tsx's
 * vite:preloadError / ViewErrorBoundary onRecover), and multi-window all
 * re-deliver the SAME logical MessageSent event. Its frontend id
 * (`${timestamp_ns}-${trace_id.slice(0,8)}`) is stable, so a re-delivery is
 * never a new message. `pushMessage` must therefore be idempotent on the id;
 * otherwise a single answer renders as two identical bubbles.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { useEventStore, type ChatMessage } from "@/store/events";

function msg(
  id: string,
  content: string,
  role: ChatMessage["role"] = "assistant",
): ChatMessage {
  return { id, role, content, ts: 1 };
}

describe("useEventStore.pushMessage idempotency", () => {
  beforeEach(() => {
    useEventStore.setState({ messages: [] });
  });

  it("renders a re-delivered message (same id) only once", () => {
    const m = msg("1700000000000-abcd1234", "Hallo, wie kann ich dir helfen?");
    useEventStore.getState().pushMessage(m);
    useEventStore.getState().pushMessage(m); // duplicate WS frame / reconnect replay

    expect(useEventStore.getState().messages).toHaveLength(1);
    expect(useEventStore.getState().messages[0]?.content).toBe(
      "Hallo, wie kann ich dir helfen?",
    );
  });

  it("keeps distinct messages that happen to share identical text", () => {
    useEventStore.getState().pushMessage(msg("a-1", "ok"));
    useEventStore.getState().pushMessage(msg("b-2", "ok"));

    expect(useEventStore.getState().messages).toHaveLength(2);
  });
});

describe("voice boot readiness (voiceReady / setVoiceReady)", () => {
  beforeEach(() => {
    // Reset to the documented default before each case.
    useEventStore.setState({ voiceReady: false });
  });

  it("defaults to false (voice boots ~20s after the window connects)", () => {
    expect(useEventStore.getState().voiceReady).toBe(false);
  });

  it("setVoiceReady(true) flips it ready, setVoiceReady(false) flips it back", () => {
    useEventStore.getState().setVoiceReady(true);
    expect(useEventStore.getState().voiceReady).toBe(true);

    useEventStore.getState().setVoiceReady(false);
    expect(useEventStore.getState().voiceReady).toBe(false);
  });
});

describe("reasoning trace (thinkingSteps / thinkingTraces)", () => {
  beforeEach(() => {
    useEventStore.setState({
      chatThinking: false,
      thinkingSteps: [],
      thinkingStartedTs: null,
      thinkingTraces: {},
    });
  });

  it("ignores events while the chat is not waiting (voice turns stay invisible)", () => {
    useEventStore
      .getState()
      .ingestThinkingEvent("ToolCallStarted", { tool_name: "wiki-recall" }, 1);
    expect(useEventStore.getState().thinkingSteps).toHaveLength(0);
  });

  it("collects steps while thinking and re-arms on a new turn", () => {
    const store = useEventStore.getState();
    store.setChatThinking(true);
    store.ingestThinkingEvent("ToolCallStarted", { tool_name: "wiki-recall" }, 1);
    expect(useEventStore.getState().thinkingSteps).toHaveLength(1);
    expect(useEventStore.getState().thinkingStartedTs).not.toBeNull();

    // A re-send starts a fresh trace — old steps belong to the superseded turn.
    store.setChatThinking(true);
    expect(useEventStore.getState().thinkingSteps).toHaveLength(0);
  });

  it("discards the live trace on timeout/error without a snapshot", () => {
    const store = useEventStore.getState();
    store.setChatThinking(true);
    store.ingestThinkingEvent("ToolCallStarted", { tool_name: "x" }, 1);
    store.setChatThinking(false);
    const s = useEventStore.getState();
    expect(s.thinkingSteps).toHaveLength(0);
    expect(Object.keys(s.thinkingTraces)).toHaveLength(0);
  });

  it("finishThinking snapshots the finalized trace onto the reply message", () => {
    const store = useEventStore.getState();
    store.setChatThinking(true);
    store.ingestThinkingEvent("ToolCallStarted", { tool_name: "wiki-recall" }, 1);
    store.finishThinking("msg-1");

    const s = useEventStore.getState();
    expect(s.chatThinking).toBe(false);
    expect(s.thinkingSteps).toHaveLength(0);
    expect(s.thinkingStartedTs).toBeNull();
    const trace = s.thinkingTraces["msg-1"];
    expect(trace).toBeDefined();
    expect(trace.steps).toHaveLength(1);
    // Active steps are finalized so the disclosure never shows a spinner.
    expect(trace.steps[0].status).toBe("done");
    expect(trace.durationMs).toBeGreaterThanOrEqual(0);
  });

  it("stores no trace for step-less fast turns (no disclosure noise)", () => {
    const store = useEventStore.getState();
    store.setChatThinking(true);
    store.finishThinking("msg-2");
    expect(useEventStore.getState().thinkingTraces["msg-2"]).toBeUndefined();
    expect(useEventStore.getState().chatThinking).toBe(false);
  });
});
