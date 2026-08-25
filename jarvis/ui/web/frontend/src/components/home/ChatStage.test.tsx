import { act, cleanup, fireEvent, render as rtlRender, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatStage } from "@/components/home/ChatStage";
import { EMPTY_TIMELINE, reduceEvents } from "@/components/agentchat/reduce";
import type { AgentChatCatalog, AgentChatEvent } from "@/lib/agentChatApi";
import { AgentChatStoreProvider } from "@/components/agentchat/AgentChatStoreContext";
import { useAgentChatStore, useAgentSessionStore } from "@/store/agentChat";
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

/**
 * The state both stores are seeded with — the front page's and the IDE's.
 *
 * Fresh per call: the two stores must never share one draft object, which is
 * the whole point of the split (jarvis surface vs agent surface).
 */
function seedState() {
  return {
    catalog: CATALOG,
    connections: [
      { jarvis: "claude-api", key_set: true, is_active_brain: true },
      { jarvis: "openai-codex", key_set: true, is_active_brain: false },
    ],
    catalogError: null,
    backendOutdated: false,
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
  };
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
    // jsdom implements neither; the column scrolls with one or the other.
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      writable: true,
      value: vi.fn(),
    });
    window.localStorage.clear();
    useEventStore.setState({ connected: true, wsWarming: false, assistantName: "Jarvis" });
    useAgentChatStore.setState(seedState());
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

  it("says who is answering: the front page names the assistant, the IDE names a coding agent", () => {
    // The maintainer's complaint, twice over: the two chats wear the same face,
    // so nothing on screen said which of them you were in (2026-08-25). They
    // are different harnesses — Jarvis with a keyboard vs a coding agent in a
    // folder — and the composer now says so before a word is typed.
    const { unmount } = render(<ChatStage />);
    const front = screen.getByTestId("composer-surface");
    expect(front.getAttribute("data-surface")).toBe("jarvis");
    expect(front.textContent).toContain("Jarvis");
    // The front page's subtitle says it is the assistant the microphone reaches.
    expect(screen.getByTestId("home-greeting").textContent).toContain("microphone");
    unmount();

    useAgentSessionStore.setState(seedState());
    render(
      <AgentChatStoreProvider store={useAgentSessionStore}>
        <ChatStage />
      </AgentChatStoreProvider>,
    );
    const ide = screen.getByTestId("composer-surface");
    expect(ide.getAttribute("data-surface")).toBe("agent");
    expect(ide.textContent).toContain("Coding agent");
    expect(ide.textContent).not.toContain("Jarvis");
    // No "Good afternoon" from a coding agent — the folder it works in instead.
    expect(screen.queryByTestId("home-greeting")).toBeNull();
    expect(screen.getByTestId("chat-folder-headline").textContent).toContain("work");
  });

  it("bylines a turn with the assistant on the front page and with the coding agent in the IDE", () => {
    const timeline = reduceEvents(EMPTY_TIMELINE, [
      ev("user_message", { text: "list the files" }),
      ev("turn_started", { turn_id: "t1", provider: "claude-api", model: "claude-opus-5", effort: "high", runner: "brain" }),
      ev("assistant_text", { turn_id: "t1", message_id: "m1", text: "Two files." }),
      ev("turn_finished", { turn_id: "t1", status: "done", duration_ms: 900, usage: {} }),
    ]);
    useAgentChatStore.setState({ activeSessionId: "s1", timeline });
    const { unmount } = render(<ChatStage />);
    expect(screen.getByTestId("agent-turn").textContent).toContain("Jarvis");
    unmount();

    useAgentSessionStore.setState({ ...seedState(), activeSessionId: "ide-1", timeline });
    render(
      <AgentChatStoreProvider store={useAgentSessionStore}>
        <ChatStage />
      </AgentChatStoreProvider>,
    );
    const turn = screen.getByTestId("agent-turn");
    expect(turn.textContent).toContain("Coding agent");
    expect(turn.textContent).not.toContain("Jarvis");
  });

  it("hides the Build | Plan switch for a provider whose ladder has no plan entry", () => {
    useAgentChatStore.setState((s) => ({ draft: { ...s.draft, provider: "openai-codex", permissionMode: "auto" } }));
    render(<ChatStage />);
    expect(screen.queryByTestId("composer-plan")).toBeNull();
  });

  it("groups the provider list by what stands behind a row: coding CLIs, API keys, local servers", async () => {
    const apiRow = {
      ...CATALOG.providers[0],
      id: "openai",
      label: "OpenAI",
      family: "openai",
      runner: "api",
      models_source: "live" as const,
      curated_models: [],
      cli_installed: null,
    };
    const localRow = { ...apiRow, id: "ollama", label: "Ollama", family: "ollama", keyless: true };
    useAgentChatStore.setState({
      catalog: { ...CATALOG, providers: [...CATALOG.providers, apiRow, localRow] },
      connections: [
        { jarvis: "claude-api", key_set: true, is_active_brain: false },
        { jarvis: "openai-codex", key_set: false, is_active_brain: false },
        { jarvis: "openai", key_set: true, is_active_brain: true },
      ],
    });
    render(<ChatStage />);
    fireEvent.click(screen.getByTestId("composer-provider"));
    const panel = await screen.findByTestId("composer-provider-panel");
    // The headings, in catalog order — never "connected / not connected".
    const headings = Array.from(panel.querySelectorAll("div.uppercase")).map((el) => el.textContent);
    expect(headings).toEqual(["Coding CLIs", "API keys", "On your own hardware"]);
    expect(within(panel).queryByText("Connected")).toBeNull();
    // Only what the Agents tab has set up is listed: Codex (no login) is not
    // there at all, and the voice sub-agent's "active" word is not shown.
    const options = within(panel).getAllByRole("option").map((el) => el.textContent ?? "");
    expect(options.map((o) => o.replace(/\s+/g, " ").trim())).toEqual(["Anthropic Claude", "OpenAI", "Ollama"]);
    expect(within(panel).queryByText("active")).toBeNull();
  });

  it("with nothing connected yet, lists every provider greyed with its connect hint", async () => {
    useAgentChatStore.setState({
      connections: [
        { jarvis: "claude-api", key_set: false, is_active_brain: false },
        { jarvis: "openai-codex", key_set: false, is_active_brain: false },
      ],
    });
    render(<ChatStage />);
    fireEvent.click(screen.getByTestId("composer-provider"));
    const panel = await screen.findByTestId("composer-provider-panel");
    const options = within(panel).getAllByRole("option");
    expect(options).toHaveLength(2);
    expect(options[0].textContent).toContain("connect");
    // Codex is not installed on this box: the hint says so instead of "connect".
    expect(options[1].textContent).toContain("not installed");
  });

  it("says when the backend is older than this window and offers the restart", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    try {
      useAgentChatStore.setState({ backendOutdated: true });
      render(<ChatStage />);
      const notice = screen.getByTestId("composer-backend-outdated");
      expect(notice.textContent).toContain("older version");
      fireEvent.click(within(notice).getByRole("button"));
      await act(async () => {});
      expect(fetchMock).toHaveBeenCalledWith("/api/settings/restart-app", { method: "POST" });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("draws a different glyph per permission mode in the pill and the list", async () => {
    render(<ChatStage />);
    const pill = screen.getByTestId("composer-permission");
    // acceptEdits wears the pen, not the column's shield.
    expect(pill.querySelector("svg.lucide-file-pen")).not.toBeNull();
    expect(pill.querySelector("svg.lucide-shield-check")).toBeNull();
    fireEvent.click(pill);
    const panel = await screen.findByTestId("composer-permission-panel");
    const glyphs = within(panel)
      .getAllByRole("option")
      .map((el) => el.querySelector("svg")?.getAttribute("class") ?? "");
    // default → question shield, acceptEdits → pen, bypass → shield off; plan lives on the switch.
    expect(glyphs.some((c) => c.includes("lucide-shield-question"))).toBe(true);
    expect(glyphs.some((c) => c.includes("lucide-file-pen"))).toBe(true);
    expect(glyphs.some((c) => c.includes("lucide-shield-off"))).toBe(true);
    expect(new Set(glyphs).size).toBe(glyphs.length);
  });

  it("wears one glyph per stance on the unified ladder", async () => {
    // The front page's catalog hands every provider the same four-step ladder
    // (jarvis/agent_chat/permissions.py, surface=jarvis); the composer draws
    // what it is given and never a ladder typed here.
    const unified = [
      { id: "ask", label: "Ask before acting", description: "" },
      { id: "accept-edits", label: "Auto-accept edits", description: "" },
      { id: "plan", label: "Plan", description: "" },
      { id: "bypass", label: "Bypass permissions", description: "" },
    ];
    useAgentChatStore.setState((s) => ({
      catalog: {
        ...CATALOG,
        providers: CATALOG.providers.map((p) => ({
          ...p,
          permission_modes: unified,
          default_permission_mode: "ask",
        })),
      },
      draft: { ...s.draft, permissionMode: "ask", buildMode: "ask" },
    }));
    render(<ChatStage />);
    const pill = screen.getByTestId("composer-permission");
    expect(pill.getAttribute("data-value")).toBe("ask");
    expect(pill.querySelector("svg.lucide-shield-question")).not.toBeNull();
    expect(pill.querySelector("svg.lucide-shield-check")).toBeNull();
    fireEvent.click(pill);
    const panel = await screen.findByTestId("composer-permission-panel");
    const rows = within(panel).getAllByRole("option");
    // Plan is the switch next door, so the list holds the other three.
    expect(rows.map((el) => el.getAttribute("data-value"))).toEqual(["ask", "accept-edits", "bypass"]);
    const glyphs = rows.map((el) => el.querySelector("svg")?.getAttribute("class") ?? "");
    expect(glyphs[0]).toContain("lucide-shield-question");
    expect(glyphs[1]).toContain("lucide-file-pen");
    expect(glyphs[2]).toContain("lucide-shield-off");
    expect(new Set(glyphs).size).toBe(glyphs.length);
    // The plan entry still powers the Build | Plan switch.
    expect(screen.getByTestId("composer-plan").getAttribute("aria-checked")).toBe("false");
  });

  it("grows the text box with the typed text and caps it at its max height", () => {
    render(<ChatStage />);
    const box = screen.getByPlaceholderText("Ask anything…") as HTMLTextAreaElement;
    // jsdom has no layout: stand in for scrollHeight and the max-height rule.
    let scrollHeight = 48;
    Object.defineProperty(box, "scrollHeight", { configurable: true, get: () => scrollHeight });
    const computed = vi.spyOn(window, "getComputedStyle").mockImplementation(
      () => ({ maxHeight: "192px" }) as CSSStyleDeclaration,
    );
    try {
      scrollHeight = 120;
      fireEvent.change(box, { target: { value: "one two three four five" } });
      expect(box.style.height).toBe("120px");
      expect(box.style.overflowY).toBe("hidden");
      scrollHeight = 400;
      fireEvent.change(box, { target: { value: "a much longer prompt".repeat(40) } });
      expect(box.style.height).toBe("192px");
      expect(box.style.overflowY).toBe("auto");
    } finally {
      computed.mockRestore();
    }
  });

  it("shows no restart notice when the catalog carries the permission ladders", () => {
    render(<ChatStage />);
    expect(screen.queryByTestId("composer-backend-outdated")).toBeNull();
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

  it("always shows that the turn is alive: glyph, a word, the clock and the tokens", () => {
    vi.useFakeTimers();
    try {
      const started = Date.now();
      const timeline = reduceEvents(EMPTY_TIMELINE, [
        ev("user_message", { text: "dig into it" }),
        { seq: 2, ts_ms: started, kind: "turn_started", payload: { turn_id: "t1", provider: "claude-api", model: "claude-opus-5", effort: "high", runner: "claude-cli" } },
        { seq: 0, ts_ms: started, kind: "reasoning_started", payload: { turn_id: "t1", message_id: "m1" } },
        { seq: 0, ts_ms: started, kind: "usage_delta", payload: { turn_id: "t1", usage: { input_tokens: 12, output_tokens: 4800 } } },
      ]);
      useAgentChatStore.setState({ activeSessionId: "s1", timeline });
      render(<ChatStage />);

      // The redacted thought is a row of its own — thinking you cannot read
      // is still thinking you can see.
      const reasoning = screen.getByTestId("agent-reasoning");
      expect(reasoning.getAttribute("data-live")).toBe("true");

      const live = screen.getByTestId("agent-turn-live");
      expect(within(live).getByTestId("agent-glyph")).toBeTruthy();
      expect(live.textContent).toMatch(/4\.8k/);
      // The clock moves on its own.
      act(() => {
        vi.advanceTimersByTime(12_000);
      });
      expect(live.textContent).toMatch(/1[0-9]s|12s/);
      // The rail marks the whole block as running.
      expect(screen.getByTestId("agent-turn-rail").className).toContain("agent-rail-live");
      // A running turn shows no receipt yet.
      expect(screen.queryByTestId("agent-turn-footer")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("names tools the way the agent's log does and closes with time and tokens", () => {
    const timeline = reduceEvents(EMPTY_TIMELINE, [
      ev("user_message", { text: "check the repo" }),
      ev("turn_started", { turn_id: "t9", provider: "claude-api", model: "claude-opus-5", effort: "high", runner: "claude-cli" }),
      ev("tool_call", { turn_id: "t9", call_id: "c1", name: "PowerShell", input: { command: "Get-ChildItem -Path 'C:\\Users'" } }),
      ev("tool_result", { turn_id: "t9", call_id: "c1", output: "ok", is_error: false }),
      ev("tool_call", { turn_id: "t9", call_id: "c2", name: "mcp__github__create_issue", input: { title: "Bug" } }),
      ev("tool_result", { turn_id: "t9", call_id: "c2", output: "#12", is_error: false }),
      ev("reasoning", { turn_id: "t9", text: "", duration_ms: 8585 }),
      ev("assistant_text", { turn_id: "t9", message_id: "m1", text: "Done." }),
      ev("turn_finished", { turn_id: "t9", status: "done", duration_ms: 12_000, usage: { input_tokens: 2, output_tokens: 219 }, cost_usd: 0.8483 }),
    ]);
    useAgentChatStore.setState({ activeSessionId: "s9", timeline });
    render(<ChatStage />);

    const tools = screen.getAllByTestId("agent-tool");
    expect(tools[0].getAttribute("data-tool")).toBe("PowerShell");
    expect(tools[0].textContent).toContain("PowerShell");
    expect(tools[0].textContent).toContain("Get-ChildItem");
    // An MCP call is named after its server, and wears its mark.
    expect(tools[1].getAttribute("data-family")).toBe("mcp");
    expect(tools[1].textContent).toContain("GitHub");

    // Thinking with no readable text still shows its time and does not open.
    const reasoning = screen.getByTestId("agent-reasoning");
    expect(reasoning.textContent).toMatch(/9s|8\.6s|8s/);
    expect(within(reasoning).getByRole("button").hasAttribute("disabled")).toBe(true);

    const footer = screen.getByTestId("agent-turn-footer");
    expect(footer.textContent).toContain("12s");
    expect(footer.textContent).toContain("219");
    expect(footer.textContent).toContain("$0.8483");
  });

  it("reads an opened voice session in the chat column instead of the previous chat", () => {
    // The regression this guards: the sidebar's history lists voice sessions
    // next to agent chats, and clicking one used to load its words into the
    // event store while the stage kept rendering the agent timeline — the
    // screen did not change at all.
    const timeline = reduceEvents(EMPTY_TIMELINE, [
      ev("user_message", { turn_id: "t1", text: "Earlier agent chat" }),
    ]);
    useAgentChatStore.setState({ activeSessionId: null, timeline });
    useEventStore.setState({
      activeKind: "voice",
      activeThreadId: "v1",
      conversations: [
        {
          kind: "voice",
          id: "v1",
          title: "Kitchen timer",
          preview: "set a timer",
          created_ms: 1,
          updated_ms: 2,
          message_count: 2,
        },
      ],
      messages: [
        { id: "m1", role: "user", content: "set a timer", ts: 1 },
        { id: "m2", role: "assistant", content: "Ten minutes, running.", ts: 2 },
      ],
      thinkingTraces: {},
    });
    render(<ChatStage />);

    const stage = screen.getByTestId("voice-thread-stage");
    expect(stage.getAttribute("data-thread")).toBe("v1");
    expect(stage.textContent).toContain("Kitchen timer");
    expect(stage.textContent).toContain("Ten minutes, running.");
    // A spoken thread has no composer: it is continued by talking.
    expect(screen.queryByTestId("agent-composer")).toBeNull();
    expect(screen.getByTestId("continue-by-voice")).toBeTruthy();
  });

  it("gives the stage back to the agent chat once a session is open", () => {
    useEventStore.setState({ activeKind: "voice", activeThreadId: "v1", messages: [] });
    useAgentChatStore.setState({ activeSessionId: "s1", timeline: EMPTY_TIMELINE });
    render(<ChatStage />);
    expect(screen.queryByTestId("voice-thread-stage")).toBeNull();
    expect(screen.getByTestId("chat-stage")).toBeTruthy();
  });
});
