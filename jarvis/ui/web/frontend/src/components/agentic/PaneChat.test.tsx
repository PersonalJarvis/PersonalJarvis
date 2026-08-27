/**
 * The chat stage: a terminal's transcript through the front page's own chat.
 *
 * What the store folds is pinned in store/paneChat.test.ts; here it is the
 * stage around it — the same `ChatStage`, so the turn, the byline and the
 * composer with its pills are the chat's own; the empty states; the way back
 * to the terminal; the notice when the pane is asking something.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "@/lib/agenticIdeApi";
import type { AgentChatEvent } from "@/lib/agentChatApi";
import type { TerminalTimelineResponse } from "@/lib/agenticIdeApi";
import { PaneChat } from "@/components/agentic/PaneChat";
import { useAgentSessionStore, type ProviderOption } from "@/store/agentChat";

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

const CLAUDE_CLI = {
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

function renderStage(props: Partial<React.ComponentProps<typeof PaneChat>> = {}) {
  const onShowTerminal = vi.fn();
  const utils = render(
    <PaneChat
      terminal="T7"
      workspaceId="ide_test"
      historyId="t7@1"
      agent="claude"
      agentLabel="Claude Code"
      folder="C:\\dev\\Personal Jarvis"
      activity=""
      onShowTerminal={onShowTerminal}
      {...props}
    />,
  );
  return { ...utils, onShowTerminal };
}

beforeEach(() => {
  vi.mocked(api.fetchTerminalTimeline).mockReset();
  vi.mocked(api.promptTerminal).mockReset();
  useAgentSessionStore.setState({
    catalog: { providers: [CLAUDE_CLI], runners: [] } as never,
    providerOptions: () => [CLAUDE_CLI],
    providerById: (id: string) => (id === CLAUDE_CLI.id ? CLAUDE_CLI : null),
    loadCatalog: async () => {},
    loadHealth: async () => {},
    loadModels: async () => {},
  });
});

afterEach(cleanup);

describe("PaneChat", () => {
  it("draws the transcript with the chat's own stage, byline and composer", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    renderStage();

    const turn = await screen.findByTestId("agent-turn");
    expect(api.fetchTerminalTimeline).toHaveBeenCalledWith("T7", "ide_test");
    expect(screen.getByTestId("chat-stage").getAttribute("data-empty")).toBe("false");
    expect(screen.getByTestId("agent-message-user").textContent).toContain("Fix the login bug");
    expect(turn.getAttribute("data-status")).toBe("done");
    expect(turn.textContent).toContain("Anthropic Claude");
    expect(turn.textContent).toContain("claude-opus-5");
    expect(turn.textContent).toContain("Looking at it now.");
    expect(turn.textContent).toContain("Found it.");
    expect(turn.textContent).toContain("src/login.ts");
    expect(screen.getByTestId("agent-turn-footer").getAttribute("data-outcome")).toBe("done");
    // The chat's composer, not a plain box: its pills show what the pane runs on.
    expect(screen.getByTestId("agent-composer")).toBeTruthy();
    expect(screen.getByTestId("composer-surface").getAttribute("data-surface")).toBe("agent");
    expect(screen.getByTestId("composer-model").textContent).toContain("Opus 5");
    expect(screen.getByTestId("composer-permission").textContent).toContain("Auto");
  });

  it("sends what is typed through the pane store", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    vi.mocked(api.promptTerminal).mockResolvedValue({
      terminal: "T7",
      sent: "Now the tests",
      composed_by: "raw",
      files: [],
      submitted: true,
    });
    renderStage();
    await screen.findByTestId("agent-turn");

    const box = screen.getByTestId("agent-composer").querySelector("textarea")!;
    fireEvent.change(box, { target: { value: "Now the tests" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await vi.waitFor(() =>
      expect(api.promptTerminal).toHaveBeenCalledWith("T7", "Now the tests", {
        compose: false,
        attachments: [],
      }),
    );
  });

  it("shows the empty page — folder headline and composer — before anything was said", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer({ events: [] }));
    renderStage();
    const stage = await screen.findByTestId("chat-stage");
    expect(stage.getAttribute("data-empty")).toBe("true");
    expect(screen.getByTestId("chat-folder-headline").textContent).toContain("Personal Jarvis");
    expect(screen.getByTestId("agent-composer")).toBeTruthy();
  });

  it("says when the CLI keeps no readable record, with the way to the terminal", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(
      answer({ readable: false, available: false, events: [] }),
    );
    const { onShowTerminal } = renderStage({ agent: "grok", agentLabel: "Grok Build" });

    const empty = await screen.findByTestId("pane-chat-not-readable");
    expect(empty.textContent).toContain("No readable record");
    expect(screen.queryByTestId("agent-composer")).toBeNull();
    fireEvent.click(screen.getByTestId("pane-chat-show-terminal"));
    expect(onShowTerminal).toHaveBeenCalledTimes(1);
  });

  it("wears the pane's title in its header, with the CLI's name beside it", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    renderStage({ title: "Fixing the login test" });
    await screen.findByTestId("agent-turn");
    const header = screen.getByTestId("pane-chat-T7").querySelector("header")!;
    expect(screen.getByTestId("pane-chat-title").textContent).toBe("Fixing the login test");
    expect(header.textContent).toContain("Claude Code");
  });

  it("names the CLI alone while no header has described the pane", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    renderStage({ title: "  " });
    await screen.findByTestId("agent-turn");
    expect(screen.queryByTestId("pane-chat-title")).toBeNull();
    const header = screen.getByTestId("pane-chat-T7").querySelector("header")!;
    expect(header.textContent).toContain("Claude Code");
  });

  it("flags a pane that is asking something only the terminal can answer", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    renderStage({ activity: "asking" });
    await screen.findByTestId("agent-turn");
    expect(screen.getByTestId("pane-chat-asking").textContent).toContain(
      "The agent is asking you something",
    );
  });
});

describe("the header's state", () => {
  // The one question the stage is opened to answer — is the agent still
  // building, or is it done? — spelled out beside the title, with how long
  // it has been so (maintainer, 2026-08-27).
  const since = Math.round(Date.now() / 1000) - 200;

  it("says the agent is still working, and for how long", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer({ live: true, activity: "working" }));
    renderStage({ activity: "working", activitySince: since, worked: true });
    await screen.findByTestId("agent-turn");
    const chip = screen.getByTestId("pane-chat-state");
    expect(chip.dataset.state).toBe("working");
    expect(chip.textContent).toContain("Working");
    expect(screen.getByTestId("pane-chat-state-for").textContent).toBe("for 3 min");
    expect(chip.querySelector("[data-icon='spinner']")).not.toBeNull();
  });

  it("says it is done once the pane has stopped after a job", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    renderStage({ activity: "waiting", activitySince: since, worked: true });
    await screen.findByTestId("agent-turn");
    const chip = screen.getByTestId("pane-chat-state");
    expect(chip.dataset.state).toBe("done");
    expect(chip.textContent).toContain("Done");
    expect(chip.querySelector("[data-icon='dot']")).not.toBeNull();
  });

  it("does not call a pane that was never asked anything done", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    renderStage({ activity: "waiting", activitySince: since, worked: false });
    await screen.findByTestId("agent-turn");
    const chip = screen.getByTestId("pane-chat-state");
    expect(chip.dataset.state).toBe("idle");
    expect(chip.textContent).toContain("Idle");
    expect(chip.textContent).not.toContain("Done");
  });

  it("says the pane needs you when it stopped on a question", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer());
    renderStage({ activity: "asking", worked: true });
    await screen.findByTestId("agent-turn");
    const chip = screen.getByTestId("pane-chat-state");
    expect(chip.dataset.state).toBe("asking");
    expect(chip.textContent).toContain("Needs you");
    // No stamp, no duration: the header never invents a number.
    expect(screen.queryByTestId("pane-chat-state-for")).toBeNull();
  });

  it("falls back to the poll's reading while the grid has none", async () => {
    vi.mocked(api.fetchTerminalTimeline).mockResolvedValue(answer({ live: true, activity: "working" }));
    renderStage({ activity: "" });
    await screen.findByTestId("agent-turn");
    expect(screen.getByTestId("pane-chat-state").dataset.state).toBe("working");
  });
});
