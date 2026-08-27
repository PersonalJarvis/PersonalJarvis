/**
 * The pane store: a terminal wearing the agent chat's store.
 *
 * Pinned here: the poll's cadence follows the pane's state, an unchanged read
 * re-renders nothing, the transcript comes out as the chat's own turn under
 * the catalog's provider, the pills say what the pane runs on, sending types
 * verbatim into the pane and reads again, Stop presses Escape, and the picks a
 * pane cannot take from outside say where to take them.
 */
import { act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "@/lib/agenticIdeApi";
import type { AgentChatEvent } from "@/lib/agentChatApi";
import type { TerminalTimelineResponse } from "@/lib/agenticIdeApi";
import { useAgentSessionStore, type ProviderOption } from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import {
  createPaneChatStore,
  eventsSignature,
  POLL_IDLE_MS,
  POLL_WORKING_MS,
  pollIntervalFor,
  providerForAgent,
} from "@/store/paneChat";

vi.mock("@/lib/agenticIdeApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/agenticIdeApi")>()),
  fetchTerminalTimeline: vi.fn(),
  promptTerminal: vi.fn(),
  interruptTerminal: vi.fn(),
  applyTerminalPicks: vi.fn(),
}));

const T0 = 1_787_800_000_000;

function ev(seq: number, kind: string, payload: Record<string, unknown>, at = 0): AgentChatEvent {
  return { seq, ts_ms: T0 + at, kind, payload };
}

/** One finished exchange, the way `agent_transcript.read_events` writes it. */
const EXCHANGE: AgentChatEvent[] = [
  ev(1, "user_message", { text: "Fix the login bug" }),
  ev(2, "turn_started", { turn_id: "turn-1", provider: "claude", model: "claude-opus-5", effort: "xhigh", runner: "cli" }, 1000),
  ev(3, "reasoning", { turn_id: "turn-1", message_id: "m1", text: "", duration_ms: 3500 }, 1000),
  ev(4, "assistant_text", { turn_id: "turn-1", message_id: "r1", text: "Looking at it now." }, 4500),
  ev(5, "tool_call", { turn_id: "turn-1", call_id: "call-1", name: "Read", input: { file_path: "src/login.ts" }, summary: "src/login.ts" }, 5000),
  ev(6, "tool_result", { turn_id: "turn-1", call_id: "call-1", output: "export const login = 1;", is_error: false, duration_ms: 2000 }, 7000),
  ev(7, "assistant_text", { turn_id: "turn-1", message_id: "r2", text: "Found it." }, 9000),
  ev(8, "turn_finished", { turn_id: "turn-1", status: "done", duration_ms: 8000, usage: { output_tokens: 80 }, error: null, cost_usd: null }, 9000),
];

const CLAUDE_CLI: ProviderOption = {
  id: "claude-api",
  label: "Anthropic Claude",
  family: "claude",
  runner: "claude-cli",
  models_source: "curated",
  curated_models: [{ id: "claude-opus-5", label: "Opus 5" }],
  default_model: "",
  effort_levels: ["low", "medium", "high", "xhigh"],
  default_effort: "high",
  permission_modes: [
    { id: "default", label: "Ask", description: "" },
    { id: "auto", label: "Auto", description: "" },
  ],
  default_permission_mode: "default",
  cli_installed: true,
  keyless: false,
  connected: true,
  active: false,
} as unknown as ProviderOption;

function answer(overrides: Partial<TerminalTimelineResponse> = {}): TerminalTimelineResponse {
  return {
    terminal: "T7",
    agent: "claude",
    readable: true,
    available: true,
    live: false,
    activity: "",
    events: EXCHANGE,
    model: "claude-opus-5",
    effort: "xhigh",
    permission_mode: "auto",
    runtime_picks: { model: true, effort: true, permission_mode: false },
    ...overrides,
  };
}

function makeStore() {
  return createPaneChatStore({
    terminal: "T7",
    workspaceId: "ide_test",
    historyId: "t7@1",
    agent: "claude",
    displayName: "Claude Code",
    folder: "C:\\dev\\Personal Jarvis",
  });
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  vi.mocked(api.fetchTerminalTimeline).mockReset();
  vi.mocked(api.promptTerminal).mockReset();
  vi.mocked(api.interruptTerminal).mockReset();
  vi.mocked(api.applyTerminalPicks).mockReset();
  useAgentSessionStore.setState({
    providerOptions: () => [CLAUDE_CLI],
    providerById: (id: string) => (id === CLAUDE_CLI.id ? CLAUDE_CLI : null),
    loadCatalog: async () => {},
    loadHealth: async () => {},
  });
  useEventStore.setState({ toasts: [] });
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
    const grown = EXCHANGE.map((e) =>
      e.seq === 7 ? { ...e, payload: { ...e.payload, text: "Found it, and fixed it." } } : e,
    );
    expect(eventsSignature(grown, false)).not.toBe(same);
  });
});

describe("providerForAgent", () => {
  it("matches a pane's CLI to the catalog row that runs that binary", () => {
    expect(providerForAgent("claude", [CLAUDE_CLI])?.id).toBe("claude-api");
    expect(providerForAgent("codex", [CLAUDE_CLI])).toBeNull();
    expect(providerForAgent("", [CLAUDE_CLI])).toBeNull();
  });

  it("matches on the row's registry key, which a runner name cannot spell", () => {
    // GLM Coding Plan runs the Claude binary and the DeepSeek harness runs
    // `dsh`: neither runner name is the agent's, so the row says whose it is.
    const glm = { ...CLAUDE_CLI, id: "glm", runner: "glm-cli", agent: "glm" } as ProviderOption;
    const dsh = {
      ...CLAUDE_CLI,
      id: "deepseek-harness",
      runner: "dsh-cli",
      agent: "deepseek-harness",
    } as ProviderOption;
    expect(providerForAgent("glm", [CLAUDE_CLI, glm, dsh])?.id).toBe("glm");
    expect(providerForAgent("deepseek-harness", [CLAUDE_CLI, glm, dsh])?.id).toBe(
      "deepseek-harness",
    );
    expect(providerForAgent("claude", [glm, CLAUDE_CLI])?.id).toBe("claude-api");
  });
});

describe("createPaneChatStore", () => {
  it("folds the transcript into the chat's turn, under the catalog provider", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    const store = makeStore();
    store.getState().start();
    await flush();
    store.getState().stop();

    const s = store.getState();
    expect(api.fetchTerminalTimeline).toHaveBeenCalledWith("T7", "ide_test");
    expect(s.pane.loading).toBe(false);
    expect(s.timeline.items.map((i) => i.type)).toEqual(["user", "turn"]);
    const turn = s.timeline.items[1];
    expect(turn.type === "turn" && turn.provider).toBe("claude-api");
    expect(turn.type === "turn" && turn.status).toBe("done");
    // The pills say what the pane runs on, in the CLI's own words.
    expect(s.draft).toMatchObject({
      provider: "claude-api",
      model: "claude-opus-5",
      effort: "xhigh",
      permissionMode: "auto",
      cwd: "C:\\dev\\Personal Jarvis",
    });
    // The stage sees a session object named after the pane, but NO agent-chat
    // session id: the composer sends that id with every file attach, and the
    // chat's session store has never heard of a pane (404 "session not
    // found", 2026-08-27).
    expect(s.activeSessionId).toBeNull();
    expect(s.activeSession?.session_id).toBe("t7@1");
    expect(s.activeSession?.model).toBe("claude-opus-5");
  });

  it("types the sentence into the pane verbatim, with its files, and reads again", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    vi.mocked(api.promptTerminal).mockResolvedValue({
      terminal: "T7",
      sent: "Now the tests",
      composed_by: "raw",
      files: [],
      submitted: true,
    });
    const store = makeStore();
    store.getState().start();
    await flush();
    const before = vi.mocked(api.fetchTerminalTimeline).mock.calls.length;

    const file = {
      name: "shot.png",
      reference: "@shot.png",
      kind: "image" as const,
      detail: "",
      described_by: "none" as const,
      note: "",
    };
    await store.getState().send("Now the tests", [file]);
    await flush();
    store.getState().stop();

    expect(api.promptTerminal).toHaveBeenCalledWith("T7", "Now the tests", {
      compose: false,
      attachments: [file],
    });
    expect(store.getState().busy).toBe(false);
    expect(store.getState().lastError).toBeNull();
    expect(vi.mocked(api.fetchTerminalTimeline).mock.calls.length).toBeGreaterThan(before);
  });

  it("says so when the pane did not take the message", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    vi.mocked(api.promptTerminal).mockResolvedValue({
      terminal: "T7",
      sent: "hi",
      composed_by: "raw",
      files: [],
      submitted: false,
      detail: "still in the input box",
    });
    const store = makeStore();
    store.getState().start();
    await flush();
    await store.getState().send("hi");
    store.getState().stop();
    const toasts = useEventStore.getState().toasts;
    expect(toasts.at(-1)?.message).toContain("T7 did not take the message: still in the input box");
    // The sentence sits in the pane's input box, not in its conversation —
    // so the echo drawn at Send is taken back, and only the record remains.
    const texts = store.getState().timeline.items.map((it) => (it.type === "user" ? it.text : it.type));
    expect(texts).toEqual(["Fix the login bug", "turn"]);
  });

  it("draws the sentence the moment Send is pressed, and hands it to the record once that holds it", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    let deliver!: (result: Awaited<ReturnType<typeof api.promptTerminal>>) => void;
    vi.mocked(api.promptTerminal).mockReturnValue(
      new Promise((resolve) => {
        deliver = resolve;
      }),
    );
    const store = makeStore();
    store.getState().start();
    await flush();
    expect(store.getState().timeline.items).toHaveLength(2);

    // Not awaited: the pane has not even typed the sentence yet.
    const sending = store.getState().send("Now the tests");
    const drawn = store.getState().timeline.items;
    expect(drawn).toHaveLength(4);
    expect(drawn[2]).toMatchObject({ type: "user", text: "Now the tests" });
    expect(drawn[3]).toMatchObject({ type: "turn", status: "running", provider: CLAUDE_CLI.id });
    expect(drawn[3].id).toMatch(/^echo-/);
    expect(store.getState().timeline.lastSeq).toBe(8); // an echo never moves the cursor

    // The pane took it; the record has not caught up: the echo stays.
    deliver({ terminal: "T7", sent: "Now the tests", composed_by: "raw", files: [], submitted: true });
    await sending;
    await flush();
    expect(store.getState().timeline.items).toHaveLength(4);
    expect(store.getState().timeline.items[3].id).toMatch(/^echo-/);

    // The record holds a message newer than before the send: its copy takes
    // over, the echo goes, and nothing is shown twice.
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(
      answer({
        live: true,
        activity: "working",
        events: [
          ...EXCHANGE,
          ev(9, "user_message", { text: "Now the tests" }, 20_000),
          ev(10, "turn_started", { turn_id: "turn-2", provider: "claude", model: "claude-opus-5", effort: "xhigh", runner: "cli" }, 20_000),
        ],
      }),
    );
    store.getState().reload();
    await flush();
    store.getState().stop();
    const items = store.getState().timeline.items;
    expect(items).toHaveLength(4);
    expect(items.filter((it) => it.type === "user" && it.text === "Now the tests")).toHaveLength(1);
    expect(items[2].id).toBe("u-9");
    expect(items[3].id).toBe("turn-2");
    expect(store.getState().timeline.lastSeq).toBe(10);
  });

  it("two messages sent back to back settle one at a time, oldest first", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    vi.mocked(api.promptTerminal).mockResolvedValue({
      terminal: "T7",
      sent: "",
      composed_by: "raw",
      files: [],
      submitted: true,
    });
    const store = makeStore();
    store.getState().start();
    await flush();
    await store.getState().send("First");
    await store.getState().send("Second");
    await flush();
    const userTexts = () =>
      store.getState().timeline.items.flatMap((it) => (it.type === "user" ? [it.text] : []));
    expect(userTexts()).toEqual(["Fix the login bug", "First", "Second"]);

    // The record has the first message and its turn; the second is still an echo.
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(
      answer({
        events: [
          ...EXCHANGE,
          ev(9, "user_message", { text: "First" }, 20_000),
          ev(10, "turn_started", { turn_id: "turn-2", provider: "claude", model: "", effort: "", runner: "cli" }, 20_000),
          ev(11, "turn_finished", { turn_id: "turn-2", status: "done", duration_ms: 10, usage: null, error: null, cost_usd: null }, 21_000),
        ],
      }),
    );
    store.getState().reload();
    await flush();
    store.getState().stop();
    expect(userTexts()).toEqual(["Fix the login bug", "First", "Second"]);
    const items = store.getState().timeline.items;
    expect(items.filter((it) => it.id.startsWith("echo-"))).toHaveLength(1);
    expect(items.filter((it) => it.type === "user" && it.id === "u-9")).toHaveLength(1);
  });

  it("withdraws the echo when the send itself fails", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    vi.mocked(api.promptTerminal).mockRejectedValue(new Error("pane is gone"));
    const store = makeStore();
    store.getState().start();
    await flush();
    await store.getState().send("hello?");
    store.getState().stop();
    expect(store.getState().lastError).toBe("pane is gone");
    expect(store.getState().timeline.items).toHaveLength(2);
  });

  it("Stop presses Escape in the pane", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer({ live: true }));
    vi.mocked(api.interruptTerminal).mockResolvedValue();
    const store = makeStore();
    await store.getState().cancel();
    expect(api.interruptTerminal).toHaveBeenCalledWith("T7", "ide_test");
  });

  it("a pick the CLI takes while it runs goes to the pane and stays on the pill", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    vi.mocked(api.applyTerminalPicks).mockImplementation(async (_name, picks) => ({
      terminal: "T7",
      applied: { ...picks },
      declined: {},
    }));
    const store = makeStore();
    store.getState().start();
    await flush();

    await store.getState().setDraft({ effort: "max" });
    expect(api.applyTerminalPicks).toHaveBeenCalledWith("T7", { effort: "max" }, "ide_test");
    expect(store.getState().draft.effort).toBe("max");

    // The record still says "xhigh" until the CLI's next reply; the pill
    // keeps the pick rather than flipping back on the next poll.
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer({ effort: "xhigh" }));
    store.getState().reload();
    await flush();
    expect(store.getState().draft.effort).toBe("max");

    // Once the record says something NEW, that is what the pill reads.
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer({ effort: "high" }));
    store.getState().reload();
    await flush();
    expect(store.getState().draft.effort).toBe("high");

    await store.getState().setDraft({ model: "claude-sonnet-5" });
    expect(api.applyTerminalPicks).toHaveBeenLastCalledWith(
      "T7",
      { model: "claude-sonnet-5" },
      "ide_test",
    );
    expect(store.getState().draft.model).toBe("claude-sonnet-5");
    store.getState().stop();
  });

  it("a pick the CLI only takes at launch is locked on the pill, and the provider always is", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    const store = makeStore();
    store.getState().start();
    await flush();

    const locks = store.getState().locks ?? {};
    expect(locks.provider).toContain("Claude Code");
    expect(locks.permissionMode).toContain("only when it starts");
    expect(locks.effort).toBeUndefined();
    expect(locks.model).toBeUndefined();

    // A stale bundle's click on a locked pill is answered with the same sentence.
    await store.getState().setDraft({ permissionMode: "default" });
    expect(api.applyTerminalPicks).not.toHaveBeenCalled();
    expect(store.getState().draft.permissionMode).toBe("auto");
    expect(useEventStore.getState().toasts.at(-1)?.message).toContain("only when it starts");

    await store.getState().setDraft({ provider: "openai-codex" });
    expect(store.getState().draft.provider).toBe("claude-api");
    expect(useEventStore.getState().toasts.at(-1)?.message).toContain("This chat runs on Claude Code");
    store.getState().stop();
  });

  it("a pick the pane declines goes back on the pill, with the pane's reason", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    vi.mocked(api.applyTerminalPicks).mockResolvedValue({
      terminal: "T7",
      applied: {},
      declined: { effort: "T7 kept `/effort max` in its input box instead of taking it." },
    });
    const store = makeStore();
    store.getState().start();
    await flush();

    await store.getState().setDraft({ effort: "max" });
    expect(store.getState().draft.effort).toBe("xhigh");
    expect(useEventStore.getState().toasts.at(-1)?.message).toContain("kept `/effort max`");

    vi.mocked(api.applyTerminalPicks).mockRejectedValue(new Error("T7 is not running right now"));
    await store.getState().setDraft({ effort: "low" });
    expect(store.getState().draft.effort).toBe("xhigh");
    expect(store.getState().lastError).toContain("not running");
    store.getState().stop();
  });

  it("keeps the column on one failed poll and re-polls on the working cadence", async () => {
    vi.useFakeTimers();
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValueOnce(answer({ live: true }));
    const store = makeStore();
    store.getState().start();
    await flush();
    expect(store.getState().timeline.items).toHaveLength(2);

    vi.mocked(api.fetchTerminalTimeline).mockRejectedValueOnce(new Error("offline"));
    await act(async () => {
      vi.advanceTimersByTime(POLL_WORKING_MS);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(store.getState().timeline.items).toHaveLength(2);
    expect(store.getState().pane.pollError).toBe("offline");
    expect(api.fetchTerminalTimeline).toHaveBeenCalledTimes(2);
    store.getState().stop();
  });
});
