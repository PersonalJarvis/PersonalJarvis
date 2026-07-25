import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// jsdom ships no ResizeObserver, and the workspace grid measures itself with
// one to decide how many panes fit side by side.
class ResizeObserverPolyfill {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = ResizeObserverPolyfill as unknown as typeof ResizeObserver;
}

// Identity translator so rendered text equals the i18n key.
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

// The workspace grid follows the app theme for its terminal colours; this test
// renders the view outside the provider, so the hook is stubbed.
vi.mock("@/hooks/useTheme", () => ({
  useTheme: () => ({ theme: "dark", setTheme: vi.fn(), toggle: vi.fn() }),
  useThemeValue: () => "dark",
}));

const pushToast = vi.fn();
const setActiveSection = vi.fn();
vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ pushToast, setActiveSection }),
}));

// Stub the heavy ChatsView import (only ViewHeader is needed).
vi.mock("@/views/ChatsView", () => ({
  ViewHeader: ({ title }: { title: string }) => <header>{title}</header>,
}));

// xterm.js needs a real canvas, which jsdom has not got — stub the panes.
vi.mock("@/components/agentic/AgenticTerminal", () => ({
  AgenticTerminal: ({ name }: { name: string }) => (
    <div data-testid={`pane-${name}`}>{name}</div>
  ),
  PaneStatusPill: () => <span>live</span>,
}));

vi.mock("@/lib/agenticIdeApi", () => ({
  fetchIdeState: vi.fn(),
  fetchIdeAgents: vi.fn(),
  fetchFolders: vi.fn(),
  searchFolders: vi.fn(),
  fetchRecents: vi.fn(),
  forgetRecent: vi.fn(),
  resolveDroppedFolder: vi.fn(),
  startIdeSession: vi.fn(),
  endIdeSession: vi.fn(),
  setFocusMode: vi.fn(),
  promptTerminal: vi.fn(),
}));

import { AgenticIdeView } from "./AgenticIdeView";
import * as api from "@/lib/agenticIdeApi";

const AGENTS: api.AgentsResponse = {
  terminal_available: true,
  max_terminals: 12,
  suggested_names: ["Mika", "Nova", "Aria", "Kai"],
  agents: [
    {
      name: "claude",
      display_name: "Claude Code",
      installed: true,
      version: "2.1.195",
      install_command: "npm install -g @anthropic-ai/claude-code",
    },
    {
      name: "codex",
      display_name: "Codex",
      installed: true,
      version: "0.142.3",
      install_command: "npm install -g @openai/codex",
    },
  ],
};

const EMPTY_STATE: api.IdeState = { active: false, session: null, max_terminals: 12 };

function sessionWith(names: string[], focus = false): api.SessionState {
  return {
    id: "ide_test",
    folder: "/work/project",
    project: {
      path: "/work/project",
      name: "project",
      exists: true,
      is_repo: true,
      branch: "main",
      stacks: ["Python"],
      instruction_files: ["CLAUDE.md"],
      top_level_dirs: ["src"],
      skills: [],
      subagents: [],
      commands: [],
      note: "",
    },
    created_at: 0,
    focus_mode: focus,
    terminals: names.map((name, index) => ({
      key: name.toLowerCase(),
      name,
      agent: "claude",
      display_name: "Claude Code",
      index,
      column: index,
      slot: 0,
      status: "live" as const,
      exit_code: null,
      error: "",
      started_at: 0,
      last_output_at: 0,
      idle_seconds: 0,
      prompts_sent: 0,
      last_prompt: "",
      lines_captured: 0,
    })),
  };
}

beforeEach(() => {
  vi.mocked(api.fetchIdeAgents).mockResolvedValue(AGENTS);
  vi.mocked(api.fetchIdeState).mockResolvedValue(EMPTY_STATE);
  vi.mocked(api.fetchFolders).mockResolvedValue({
    path: null,
    parent: null,
    entries: [
      { name: "project", path: "/work/project", is_project: true, is_repo: true },
    ],
    device_name: "Rubens MacBook",
  });
  vi.mocked(api.fetchRecents).mockResolvedValue({
    device_name: "Rubens MacBook",
    recents: [],
  });
  vi.mocked(api.searchFolders).mockResolvedValue({
    query: "",
    entries: [],
    truncated: false,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Agentic IDE wizard", () => {
  it("starts on the folder step and blocks Next until a folder is picked", async () => {
    render(<AgenticIdeView />);
    await waitFor(() => expect(api.fetchIdeAgents).toHaveBeenCalled());

    const next = screen.getByRole("button", { name: /next/i }) as HTMLButtonElement;
    expect(next.disabled).toBe(true);

    fireEvent.click(await screen.findByRole("button", { name: /project/i }));
    await waitFor(() => expect(next.disabled).toBe(false));
  });

  it("walks folder → count → agents → start and opens the workspace", async () => {
    vi.mocked(api.startIdeSession).mockResolvedValue(sessionWith(["Mika", "Nova"]));
    render(<AgenticIdeView />);
    await waitFor(() => expect(api.fetchIdeAgents).toHaveBeenCalled());

    fireEvent.click(await screen.findByRole("button", { name: /project/i }));
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Count step — pick 2 terminals.
    fireEvent.click(screen.getByRole("button", { name: "2" }));
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    // Agent step — the call-signs are pre-filled from the suggested pool.
    expect(screen.getByDisplayValue("Mika")).toBeTruthy();
    expect(screen.getByDisplayValue("Nova")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    fireEvent.click(screen.getByRole("button", { name: /open workspace/i }));

    await waitFor(() =>
      expect(api.startIdeSession).toHaveBeenCalledWith("/work/project", [
        { agent: "claude", name: "Mika" },
        { agent: "claude", name: "Nova" },
      ]),
    );
    // Panes are rendered once the session exists.
    expect(await screen.findByTestId("pane-Mika")).toBeTruthy();
  });

  /**
   * Drive the wizard to the count step with a workspace of ``width`` pixels,
   * choose ``n`` through a preset or Custom Terminals, and return its dot grid.
   */
  async function countTile(width: number, n: string): Promise<HTMLElement> {
    const previous = globalThis.ResizeObserver;
    class WidthObserver {
      constructor(private readonly callback: ResizeObserverCallback) {}
      observe(): void {
        this.callback(
          [{ contentRect: { width } } as ResizeObserverEntry],
          this as unknown as ResizeObserver,
        );
      }
      unobserve(): void {}
      disconnect(): void {}
    }
    globalThis.ResizeObserver = WidthObserver as unknown as typeof ResizeObserver;
    try {
      render(<AgenticIdeView />);
      await waitFor(() => expect(api.fetchIdeAgents).toHaveBeenCalled());
      fireEvent.click(await screen.findByRole("button", { name: /project/i }));
      fireEvent.click(screen.getByRole("button", { name: /next/i }));
      const preset = screen.queryByRole("button", { name: n });
      if (preset) return preset.querySelector("div") as HTMLElement;

      fireEvent.click(screen.getByRole("button", { name: /custom terminals/i }));
      fireEvent.change(screen.getByRole("spinbutton", { name: /custom terminal count/i }), {
        target: { value: n },
      });
      return screen.getByTestId("custom-terminal-preview");
    } finally {
      globalThis.ResizeObserver = previous;
    }
  }

  /** The number of columns promised by a preview dot grid. */
  const dotsOf = (grid: HTMLElement) => grid.style.gridTemplateColumns;

  it("keeps uncommon counts in Custom Terminals instead of fixed cards", async () => {
    await countTile(2328, "10");

    expect(screen.queryByRole("button", { name: "10" })).toBeNull();
    expect(screen.queryByRole("button", { name: "12" })).toBeNull();
    expect(screen.getByRole("button", { name: /custom terminals/i })).toBeTruthy();
    expect(
      (screen.getByRole("spinbutton", {
        name: /custom terminal count/i,
      }) as HTMLInputElement).value,
    ).toBe("10");
  });

  it("builds the requested number of custom terminal plans", async () => {
    render(<AgenticIdeView />);
    await waitFor(() => expect(api.fetchIdeAgents).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole("button", { name: /project/i }));
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    fireEvent.click(screen.getByRole("button", { name: /custom terminals/i }));
    fireEvent.change(screen.getByRole("spinbutton", { name: /custom terminal count/i }), {
      target: { value: "10" },
    });
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    expect(screen.getByLabelText("Call-sign for terminal 10")).toBeTruthy();
  });

  it("caps custom terminal counts at the backend limit", async () => {
    await countTile(2328, "99");

    expect(
      (screen.getByRole("spinbutton", {
        name: /custom terminal count/i,
      }) as HTMLInputElement).value,
    ).toBe("12");
    expect(
      (screen.getByRole("button", { name: /use one more terminal/i }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("previews 12 terminals as 6 and 6 when the window is wide enough", async () => {
    // 2328 px of workspace minus the grid's padding fits six 380 px panes, so
    // the twelve wrap into two even bands — the arrangement the user asked for.
    const tile = await countTile(2328, "12");
    expect(dotsOf(tile)).toBe("repeat(6, minmax(0, 1fr))");
  });

  it("previews the narrower arrangement a narrow window will really produce", async () => {
    // The same twelve in a 1314 px workspace: only three panes stay readable
    // per line, so the grid makes 3 × 4. The preview promised 6 + 6 here once,
    // which is the drift this test exists to catch.
    const tile = await countTile(1314, "12");
    expect(dotsOf(tile)).toBe("repeat(3, minmax(0, 1fr))");
  });

  it("keeps a small workspace on one line at any usable width", async () => {
    const tile = await countTile(1314, "3");
    expect(dotsOf(tile)).toBe("repeat(3, minmax(0, 1fr))");
  });

  it("says so plainly when the machine has no terminal backend", async () => {
    vi.mocked(api.fetchIdeAgents).mockResolvedValue({
      ...AGENTS,
      terminal_available: false,
    });
    render(<AgenticIdeView />);
    expect(await screen.findByText(/no usable terminal backend/i)).toBeTruthy();
  });

  it("points at the CLIs page when no coding agent is installed", async () => {
    vi.mocked(api.fetchIdeAgents).mockResolvedValue({
      ...AGENTS,
      agents: AGENTS.agents.map((a) => ({ ...a, installed: false })),
    });
    render(<AgenticIdeView />);
    fireEvent.click(await screen.findByRole("button", { name: /open clis/i }));
    expect(setActiveSection).toHaveBeenCalledWith("clis");
  });
});

describe("Agentic IDE running workspace", () => {
  it("renders the open session instead of the wizard", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue({
      active: true,
      session: sessionWith(["Mika"]),
      max_terminals: 12,
    });
    render(<AgenticIdeView />);
    expect(await screen.findByTestId("pane-Mika")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /next/i })).toBeNull();
  });

  it("toggles focus mode through the API, not just locally", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue({
      active: true,
      session: sessionWith(["Mika"]),
      max_terminals: 12,
    });
    vi.mocked(api.setFocusMode).mockResolvedValue(true);
    render(<AgenticIdeView />);

    const toggle = await screen.findByTestId("agentic-focus-toggle");
    // The view turns coding mode on by itself when a workspace opens, so the
    // starting state is not asserted here — what matters is that the switch goes
    // through the API rather than only flipping local state.
    await waitFor(() => expect(api.setFocusMode).toHaveBeenCalled());
    const before = toggle.getAttribute("aria-pressed") === "true";
    vi.mocked(api.setFocusMode).mockResolvedValue(!before);

    fireEvent.click(toggle);
    await waitFor(() => expect(api.setFocusMode).toHaveBeenLastCalledWith(!before));
    await waitFor(() =>
      expect(toggle.getAttribute("aria-pressed")).toBe(String(!before)),
    );
  });

  it("sends a prompt to the selected terminal through the same endpoint voice uses", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue({
      active: true,
      session: sessionWith(["Mika", "Nova"]),
      max_terminals: 12,
    });
    vi.mocked(api.promptTerminal).mockResolvedValue({
      terminal: "Mika",
      sent: "run the tests",
      composed_by: "raw",
      files: [],
      submitted: true,
    });
    render(<AgenticIdeView />);

    await screen.findByTestId("pane-Mika");
    const box = screen.getByPlaceholderText(/type an instruction for mika/i);
    fireEvent.change(box, { target: { value: "run the tests" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() =>
      expect(api.promptTerminal).toHaveBeenCalledWith("Mika", "run the tests"),
    );
  });

  it("reports a refused prompt instead of pretending it landed", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue({
      active: true,
      session: sessionWith(["Mika"]),
      max_terminals: 12,
    });
    vi.mocked(api.promptTerminal).mockRejectedValue(
      new Error("Mika is not running right now"),
    );
    render(<AgenticIdeView />);

    await screen.findByTestId("pane-Mika");
    fireEvent.change(screen.getByPlaceholderText(/type an instruction/i), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith("error", "Mika is not running right now"),
    );
  });
  it("shows panes that voice opened, without a reload", async () => {
    /*
     * The regression this guards: a spoken "spawn three more terminals" adds the
     * panes in the backend, but this view fetches its state once on mount. Before
     * the listener existed, the agents were running and the user saw the old grid
     * — the feature looked broken while working perfectly.
     */
    vi.mocked(api.fetchIdeState).mockResolvedValue({
      active: true,
      session: sessionWith(["Mika", "Nova"]),
      max_terminals: 12,
    });
    render(<AgenticIdeView />);
    await screen.findByTestId("pane-Mika");
    expect(screen.queryByTestId("pane-Aria")).toBeNull();

    // Voice opened a third pane; the WebSocket layer turns the bus event into
    // this window event.
    vi.mocked(api.fetchIdeState).mockResolvedValue({
      active: true,
      session: sessionWith(["Mika", "Nova", "Aria"]),
      max_terminals: 12,
    });
    window.dispatchEvent(
      new CustomEvent("jarvis:agentic-ide-changed", {
        detail: { names: ["Aria"], agent: "claude" },
      }),
    );

    await screen.findByTestId("pane-Aria");
    // The panes that were already mounted stay mounted: re-parenting one would
    // tear down its WebSocket and kill the agent behind it.
    expect(screen.getByTestId("pane-Mika")).toBeTruthy();
  });
});
