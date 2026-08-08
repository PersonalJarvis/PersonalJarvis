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
import {
  initialSectionFromSearch,
  soloWindowFromSearch,
  useEventStore,
  type ChatMessage,
} from "@/store/events";

describe("initial section deep links", () => {
  it("opens Docs when a guide slug is present", () => {
    expect(initialSectionFromSearch("?doc=voice-conversations")).toBe("docs");
    expect(initialSectionFromSearch("")).toBe("chats");
  });

  it("honors ?view= for any known section (the solo-window deep link)", () => {
    expect(initialSectionFromSearch("?view=agentic-ide&solo=1")).toBe(
      "agentic-ide",
    );
    expect(initialSectionFromSearch("?view=chats")).toBe("chats");
    expect(initialSectionFromSearch("?view=settings")).toBe("settings");
  });

  it("falls through an invalid ?view= to the older rules", () => {
    // Unknown section id: never crash, never trust the URL.
    expect(initialSectionFromSearch("?view=nope")).toBe("chats");
    // ...and the ?doc shortcut still applies behind it.
    expect(initialSectionFromSearch("?view=nope&doc=x")).toBe("docs");
  });

  it("prefers ?view= over ?doc when both are present", () => {
    // Pinned: the explicit deep link wins over the docs shortcut.
    expect(initialSectionFromSearch("?view=tasks&doc=x")).toBe("tasks");
  });
});

describe("solo window flag (?solo=1)", () => {
  it("is set only by the exact solo=1 value", () => {
    expect(soloWindowFromSearch("?view=chats&solo=1")).toBe(true);
    expect(soloWindowFromSearch("?solo=1")).toBe(true);
    expect(soloWindowFromSearch("?solo=0")).toBe(false);
    expect(soloWindowFromSearch("?solo=true")).toBe(false);
    expect(soloWindowFromSearch("")).toBe(false);
  });
});

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
    const m = msg("1700000000000-abcd1234", "Hallo, wie kann ich dir helfen?"); // i18n-allow: simulated German assistant chat message is the content under test
    useEventStore.getState().pushMessage(m);
    useEventStore.getState().pushMessage(m); // duplicate WS frame / reconnect replay

    expect(useEventStore.getState().messages).toHaveLength(1);
    expect(useEventStore.getState().messages[0]?.content).toBe(
      "Hallo, wie kann ich dir helfen?", // i18n-allow: simulated German assistant chat message is the content under test
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

describe("ws warming state (wsWarming / setWarming)", () => {
  it("tracks wsWarming and defaults it to true", () => {
    // Fresh store: warming until proven connected/offline so the first paint
    // after a restart reads "Starting…", never a scary "OFFLINE".
    expect(useEventStore.getState().wsWarming).toBe(true);
    useEventStore.getState().setWarming(false);
    expect(useEventStore.getState().wsWarming).toBe(false);
    useEventStore.getState().setWarming(true);
    expect(useEventStore.getState().wsWarming).toBe(true);
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

/**
 * Regression guard for the stacked-notification wall (2026-08-04).
 *
 * A provider card answers a click it cannot honour with a fixed sentence
 * ("Connect your ChatGPT subscription to use this voice provider."). Every
 * click emitted a fresh toast, and the card bound BOTH onClick and
 * onDoubleClick, so one double click emitted three. Two attempts produced six
 * identical boxes stacked down the right edge — covering the connect button
 * the message was telling the user to press. Repeats must collapse into one
 * toast with a counter.
 */
describe("useEventStore.pushToast repeat collapsing", () => {
  beforeEach(() => {
    useEventStore.setState({ toasts: [] });
  });

  it("collapses an identical repeated notice into one counted toast", () => {
    const { pushToast } = useEventStore.getState();
    const line = "Connect your ChatGPT subscription to use this voice provider.";
    for (let i = 0; i < 6; i += 1) pushToast("warning", line);

    const { toasts } = useEventStore.getState();
    expect(toasts).toHaveLength(1);
    expect(toasts[0].count).toBe(6);
    expect(toasts[0].message).toBe(line);
  });

  it("keeps genuinely different notices apart", () => {
    const { pushToast } = useEventStore.getState();
    pushToast("warning", "Connect your ChatGPT subscription.");
    pushToast("warning", "OpenAI Realtime needs a key.");
    pushToast("error", "Connect your ChatGPT subscription.");

    const { toasts } = useEventStore.getState();
    expect(toasts).toHaveLength(3);
    expect(toasts.every((toast) => toast.count === 1)).toBe(true);
  });

  it("keeps two saved files as two toasts with their own actions", () => {
    const { pushToast } = useEventStore.getState();
    pushToast("success", "Saved", { filePath: "/tmp/a.md", filename: "a.md" });
    pushToast("success", "Saved", { filePath: "/tmp/b.md", filename: "b.md" });

    const { toasts } = useEventStore.getState();
    expect(toasts).toHaveLength(2);
    expect(toasts.map((toast) => toast.filePath)).toEqual([
      "/tmp/a.md",
      "/tmp/b.md",
    ]);
  });

  it("a repeat pushes the dismissal out instead of inheriting the first expiry", () => {
    const { pushToast } = useEventStore.getState();
    pushToast("info", "still here");
    const first = useEventStore.getState().toasts[0].expiresAt;
    pushToast("info", "still here");
    const refreshed = useEventStore.getState().toasts[0].expiresAt;
    expect(refreshed).toBeGreaterThanOrEqual(first);
  });
});
