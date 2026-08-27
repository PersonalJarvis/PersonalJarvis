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
    await store.getState().send("hi");
    const toasts = useEventStore.getState().toasts;
    expect(toasts.at(-1)?.message).toContain("T7 did not take the message: still in the input box");
  });

  it("Stop presses Escape in the pane", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer({ live: true }));
    vi.mocked(api.interruptTerminal).mockResolvedValue();
    const store = makeStore();
    await store.getState().cancel();
    expect(api.interruptTerminal).toHaveBeenCalledWith("T7", "ide_test");
  });

  it("a model pick is typed in as the CLI's own command; the other picks say where to go", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    vi.mocked(api.promptTerminal).mockResolvedValue({
      terminal: "T7",
      sent: "/model claude-sonnet-5",
      composed_by: "raw",
      files: [],
      submitted: true,
    });
    const store = makeStore();
    store.getState().start();
    await flush();

    await store.getState().setDraft({ model: "claude-sonnet-5" });
    expect(api.promptTerminal).toHaveBeenCalledWith("T7", "/model claude-sonnet-5", { compose: false });
    expect(store.getState().draft.model).toBe("claude-sonnet-5");

    await store.getState().setDraft({ permissionMode: "default" });
    expect(store.getState().draft.permissionMode).toBe("auto");
    const toasts = useEventStore.getState().toasts;
    expect(toasts.at(-1)?.message).toContain("change it in the terminal");
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
