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
