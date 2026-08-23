import { act, cleanup, fireEvent, render as rtlRender, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatStage } from "@/components/home/ChatStage";
import { EMPTY_TIMELINE, reduceEvents } from "@/components/agentchat/reduce";
import type { AgentChatCatalog, AgentChatEvent } from "@/lib/agentChatApi";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore } from "@/store/events";

const CATALOG: AgentChatCatalog = {
  default_cwd: "C:\\work",
  shell: "pwsh",
  providers: [
    {
      id: "claude-api",
      label: "Anthropic Claude",
      family: "claude",
      runner: "claude-cli",
      models_source: "curated",
      curated_models: [
        { id: "claude-opus-5", label: "Claude Opus 5" },
        { id: "claude-sonnet-5", label: "Claude Sonnet 5" },
      ],
      default_model: "",
      keyless: false,
      native_resume: true,
      effort_levels: ["low", "medium", "high", "xhigh", "max"],
      default_effort: "high",
      permission_modes: [
        { id: "default", label: "Ask before acting", description: "" },
        { id: "acceptEdits", label: "Auto-accept edits", description: "" },
        { id: "plan", label: "Plan", description: "" },
        { id: "bypassPermissions", label: "Bypass permissions", description: "" },
      ],
      default_permission_mode: "acceptEdits",
      cli_installed: true,
    },
    {
      id: "openai-codex",
      label: "OpenAI Codex",
      family: "openai",
      runner: "codex-cli",
      models_source: "curated",
      curated_models: [{ id: "gpt-5.4", label: "GPT-5.4" }],
      default_model: "",
      keyless: false,
      native_resume: true,
      effort_levels: ["minimal", "low", "medium", "high", "xhigh"],
      default_effort: "medium",
      permission_modes: [
        { id: "read-only", label: "Read only", description: "" },
        { id: "auto", label: "Auto", description: "" },
        { id: "full-access", label: "Full access", description: "" },
      ],
      default_permission_mode: "auto",
      cli_installed: false,
    },
  ],
};

// The greeting reads the profile name (a query), so the stage mounts under a
// query client like the app.
function render(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

let seq = 0;
function ev(kind: string, payload: Record<string, unknown>): AgentChatEvent {
  seq += 1;
  return { seq, ts_ms: 1_000 + seq, kind, payload };
}

describe("ChatStage (agent chat)", () => {
  let scrollTo: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    scrollTo = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      writable: true,
      value: scrollTo,
    });
    window.localStorage.clear();
    useEventStore.setState({ connected: true, wsWarming: false, assistantName: "Jarvis" });
    useAgentChatStore.setState({
      catalog: CATALOG,
      connections: [
        { jarvis: "claude-api", key_set: true, is_active_brain: true },
        { jarvis: "openai-codex", key_set: true, is_active_brain: false },
      ],
      catalogError: null,
      liveModels: {},
      sessions: [],
      activeSessionId: null,
      activeSession: null,
      timeline: EMPTY_TIMELINE,
      draft: {
        provider: "claude-api",
        model: "",
        effort: "high",
        permissionMode: "acceptEdits",
        buildMode: "acceptEdits",
        cwd: "C:\\work",
      },
      busy: false,
      lastError: null,
      // The stage's mount effects fetch; keep them inert here.
      loadCatalog: async () => {},
      loadSessions: async () => {},
      loadModels: async () => {},
    });
  });
  afterEach(() => {
    cleanup();
  });

  it("shows the greeting and the composer with the four picks when there is nothing yet", () => {
    render(<ChatStage />);
    expect(screen.getByTestId("chat-stage").getAttribute("data-empty")).toBe("true");
    const composer = screen.getByTestId("agent-composer");
    expect(within(composer).getByTestId("composer-provider").getAttribute("data-value")).toBe("claude-api");
    expect(within(composer).getByTestId("composer-model")).toBeTruthy();
    expect(within(composer).getByTestId("composer-effort").getAttribute("data-value")).toBe("high");
    expect(within(composer).getByTestId("composer-permission").getAttribute("data-value")).toBe("acceptEdits");
    // Claude Code has a plan entry, so the Build | Plan switch is drawn.
    expect(within(composer).getByTestId("composer-plan").getAttribute("aria-checked")).toBe("false");
  });

  it("hides the Build | Plan switch for a provider whose ladder has no plan entry", () => {
    useAgentChatStore.setState((s) => ({ draft: { ...s.draft, provider: "openai-codex", permissionMode: "auto" } }));
    render(<ChatStage />);
    expect(screen.queryByTestId("composer-plan")).toBeNull();
  });

  it("greys out a provider that is not connected and says how to fix it", () => {
    useAgentChatStore.setState({
      connections: [{ jarvis: "claude-api", key_set: false, is_active_brain: true }],
    });
    render(<ChatStage />);
    const hint = screen.getByTestId("composer-connect-hint");
    expect(hint.textContent).toContain("Anthropic Claude");
    // Send stays off until the provider is usable.
    expect((screen.getByTestId("composer-send") as HTMLButtonElement).disabled).toBe(true);
  });

  it("renders the folded timeline: user bubble, byline with provider + model, tool row, answer", () => {
    const timeline = reduceEvents(EMPTY_TIMELINE, [
      ev("user_message", { text: "list the files" }),
      ev("turn_started", { turn_id: "t1", provider: "claude-api", model: "claude-opus-5", effort: "high", runner: "claude-cli" }),
      ev("tool_call", { turn_id: "t1", call_id: "c1", name: "Bash", input: { command: "ls" } }),
      ev("tool_result", { turn_id: "t1", call_id: "c1", output: "a.py\nb.py", is_error: false, duration_ms: 40 }),
      ev("assistant_text", { turn_id: "t1", message_id: "m1", text: "Two files: **a.py** and b.py." }),
      ev("turn_finished", { turn_id: "t1", status: "done", duration_ms: 1200, usage: {} }),
    ]);
    useAgentChatStore.setState({ activeSessionId: "s1", timeline });
    render(<ChatStage />);
    expect(screen.getByTestId("chat-stage").getAttribute("data-empty")).toBe("false");
    expect(screen.getByTestId("agent-message-user").textContent).toContain("list the files");
    const turn = screen.getByTestId("agent-turn");
    expect(turn.textContent).toContain("Anthropic Claude");
    expect(turn.textContent).toContain("claude-opus-5");
    const tool = within(turn).getByTestId("agent-tool");
    expect(tool.getAttribute("data-tool")).toBe("Bash");
    expect(tool.getAttribute("data-state")).toBe("done");
    // The row opens to show input and output.
    fireEvent.click(within(tool).getByRole("button"));
    expect(tool.textContent).toContain("a.py");
    expect(within(turn).getByTestId("agent-text").querySelector("strong")?.textContent).toBe("a.py");
    // The person's message was pinned to the top of the scroll area.
    expect(scrollTo).toHaveBeenCalled();
  });

  it("shows the approval card while the runner waits and routes the click to the store", () => {
    const decide = vi.fn(async () => {});
    const timeline = reduceEvents(EMPTY_TIMELINE, [
      ev("user_message", { text: "delete it" }),
      ev("turn_started", { turn_id: "t2", provider: "openai", model: "", effort: "", runner: "api" }),
      ev("approval_required", { turn_id: "t2", approval_id: "a1", call_id: "c2", name: "RunCommand", input: { command: "rm x" }, summary: "rm x" }),
    ]);
    useAgentChatStore.setState({ activeSessionId: "s2", timeline, decide });
    render(<ChatStage />);
    const card = screen.getByTestId("agent-approval");
    expect(card.textContent).toContain("rm x");
    act(() => {
      fireEvent.click(within(card).getByTestId("approval-allow"));
    });
    expect(decide).toHaveBeenCalledWith("a1", "allow");
    // The composer offers Stop while the turn runs.
    expect(screen.getByTestId("composer-stop")).toBeTruthy();
  });
});
