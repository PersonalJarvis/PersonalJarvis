import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// jsdom ships no ResizeObserver, and the workspace grid measures itself with
// one to decide how many panes fit side by side.
class ResizeObserverPolyfill {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver =
    ResizeObserverPolyfill as unknown as typeof ResizeObserver;
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
  fetchResumeOffer: vi.fn(),
  resumeWorkspace: vi.fn(),
  forgetResumeOffer: vi.fn(),
  fetchWorkspaces: vi.fn(),
  activateWorkspace: vi.fn(),
  renameWorkspace: vi.fn(),
  closeWorkspace: vi.fn(),
  fetchNativePickerSupport: vi.fn(),
  openNativePicker: vi.fn(),
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

const EMPTY_STATE: api.IdeState = {
  active: false,
  session: null,
  max_terminals: 12,
  workspaces: [],
  active_id: null,
  max_workspaces: 6,
};

/**
 * The state the backend returns with one workspace open.
 *
 * Derives the workspace bar from the session rather than letting a test spell
 * both out, so a fixture can never describe a front workspace that is not in
 * the bar — a shape the backend cannot produce and a test should not either.
 */
function stateWith(session: api.SessionState): api.IdeState {
  return {
    active: true,
    session,
    max_terminals: 12,
    workspaces: [
      {
        id: session.id,
        folder: session.folder,
        name: session.project.name,
        branch: session.project.branch,
        terminals: session.terminals.length,
        live_terminals: session.terminals.filter((t) => t.status === "live")
          .length,
        focus_mode: session.focus_mode,
        created_at: session.created_at,
        last_active_at: session.created_at,
        active: true,
      },
    ],
    active_id: session.id,
    max_workspaces: 6,
  };
}

const NO_OFFER: api.ResumeOffer = {
  available: false,
  saved_at: 0,
  workspace_count: 0,
  terminal_count: 0,
  resumable_count: 0,
  workspaces: [],
};

const PREVIOUS_WORKSPACE: api.ResumeOffer = {
  available: true,
  saved_at: 1_753_473_600,
  workspace_count: 1,
  terminal_count: 2,
  resumable_count: 1,
  workspaces: [
    {
      session_id: "ide_old",
      folder: "/work/project",
      folder_name: "project",
      folder_exists: true,
      available: true,
      resumable_count: 1,
      terminals: [
        {
          key: "alex",
          name: "Alex",
          agent: "claude",
          display_name: "Claude Code",
          column: 0,
          slot: 0,
          available: true,
          resumable: true,
          prompts_sent: 2,
        },
        {
          key: "blake",
          name: "Blake",
          agent: "claude",
          display_name: "Claude Code",
          column: 1,
          slot: 0,
          available: true,
          resumable: false,
          prompts_sent: 0,
        },
      ],
    },
  ],
};

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
      {
        name: "project",
        path: "/work/project",
        is_project: true,
        is_repo: true,
      },
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
  // Nothing to resume by default; the resume tests override this.
  vi.mocked(api.fetchResumeOffer).mockResolvedValue(NO_OFFER);
  // No system folder window in jsdom — the picker falls back to browsing.
  vi.mocked(api.fetchNativePickerSupport).mockResolvedValue({
    available: false,
    reason: "not available under test",
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

    const next = screen.getByRole("button", {
      name: /next/i,
    }) as HTMLButtonElement;
    expect(next.disabled).toBe(true);

    fireEvent.click(await screen.findByRole("button", { name: /project/i }));
    await waitFor(() => expect(next.disabled).toBe(false));
  });

  it("walks folder → count → agents → start and opens the workspace", async () => {
    vi.mocked(api.startIdeSession).mockResolvedValue(
      stateWith(sessionWith(["Mika", "Nova"])),
    );
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
   * Drive the wizard to the count step in a workspace of ``width`` pixels, set
   * the count to ``n``, and return the stage — the miniature of the workspace
   * that is about to open.
   */
  async function stageAt(width: number, n: string): Promise<HTMLElement> {
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
    globalThis.ResizeObserver =
      WidthObserver as unknown as typeof ResizeObserver;
    try {
      render(<AgenticIdeView />);
      await waitFor(() => expect(api.fetchIdeAgents).toHaveBeenCalled());
      fireEvent.click(await screen.findByRole("button", { name: /project/i }));
      fireEvent.click(screen.getByRole("button", { name: /next/i }));
      fireEvent.change(
        screen.getByRole("spinbutton", { name: /number of terminals/i }),
        { target: { value: n } },
      );
      return screen.getByTestId("workspace-stage-grid");
    } finally {
      globalThis.ResizeObserver = previous;
    }
  }

  /** Columns and bands the stage lays its panes out in. */
  const columnsOf = (stage: HTMLElement) => stage.style.gridTemplateColumns;
  const rowsOf = (stage: HTMLElement) => stage.style.gridTemplateRows;

  /**
   * A pixel width the way the readout writes it.
   *
   * Widths are grouped with a NARROW NO-BREAK SPACE (U+202F) so a number
   * never breaks across two lines. Spelled as an escape rather than pasted:
   * the two characters are indistinguishable in an editor, and only one of
   * them matches.
   */
  const grouped = (digits: string) => digits.replace(/ /g, "\u202F");

  it("sets any count from one control instead of cards plus a custom row", async () => {
    // Two ways to set one number meant two competing "selected" states. The
    // track covers every value, and the common ones are notches on it.
    await stageAt(2328, "10");

    expect(
      (
        screen.getByRole("slider", {
          name: /number of terminals/i,
        }) as HTMLInputElement
      ).value,
    ).toBe("10");
    expect(screen.getByTestId("terminal-count-value").textContent).toBe("10");
    expect(
      screen.queryByRole("button", { name: /custom terminals/i }),
    ).toBeNull();
  });

  it("builds the requested number of terminal plans", async () => {
    render(<AgenticIdeView />);
    await waitFor(() => expect(api.fetchIdeAgents).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole("button", { name: /project/i }));
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    fireEvent.change(
      screen.getByRole("spinbutton", { name: /number of terminals/i }),
      { target: { value: "10" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    expect(screen.getByLabelText("Call-sign for terminal 10")).toBeTruthy();
  });

  it("caps the count at the backend limit", async () => {
    await stageAt(2328, "99");

    expect(
      (
        screen.getByRole("spinbutton", {
          name: /number of terminals/i,
        }) as HTMLInputElement
      ).value,
    ).toBe("12");
    expect(
      (
        screen.getByRole("button", {
          name: /use one more terminal/i,
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });

  it("previews 12 terminals as 6 and 6 when the window is wide enough", async () => {
    // 2328 px of workspace minus the grid's padding fits six 380 px panes, so
    // the twelve wrap into two even bands — the arrangement the user asked for.
    const stage = await stageAt(2328, "12");
    expect(columnsOf(stage)).toBe("repeat(6, minmax(0, 1fr))");
    expect(rowsOf(stage)).toBe("repeat(2, minmax(0, 1fr))");
  });

  it("previews the narrower arrangement a narrow window will really produce", async () => {
    // The same twelve in a 1314 px workspace: only three panes stay readable
    // per line, so the grid makes 3 × 4. The preview promised 6 + 6 here once,
    // which is the drift this test exists to catch.
    const stage = await stageAt(1314, "12");
    expect(columnsOf(stage)).toBe("repeat(3, minmax(0, 1fr))");
    expect(rowsOf(stage)).toBe("repeat(4, minmax(0, 1fr))");
  });

  it("keeps a small workspace on one line at any usable width", async () => {
    const stage = await stageAt(1314, "3");
    expect(columnsOf(stage)).toBe("repeat(3, minmax(0, 1fr))");
    expect(rowsOf(stage)).toBe("repeat(1, minmax(0, 1fr))");
  });

  it("draws one pane per terminal and never more than the stage can hold", async () => {
    // The old dot preview sat in a fixed 40×40 px box with nothing bounding it,
    // so a high count in a narrow window grew a tall column of dots straight out
    // through the card, over the buttons above and below. The stage divides a
    // FIXED height between its rows instead, so no count can overflow it — the
    // panes get thinner, the box does not grow.
    const stage = await stageAt(800, "12");
    expect(stage.children.length).toBe(12);
    expect(columnsOf(stage)).toBe("repeat(2, minmax(0, 1fr))");
    expect(rowsOf(stage)).toBe("repeat(6, minmax(0, 1fr))");
  });

  it("names the window width its arrangement depends on", async () => {
    // The reported bug: the preview said "2 across, 4 down" in a 1050 px window
    // and the workspace opened 4 × 2 once maximised. Both are right — what was
    // missing is that the arrangement has a condition at all.
    await stageAt(1050, "8");

    const readout = screen.getByTestId("workspace-stage-readout");
    expect(readout.textContent).toContain("2 across");
    expect(readout.textContent).toContain("4 down");
    // 8 × 380 px + the grid's padding — the width at which they all fit on one
    // line, stated so a maximise cannot turn the preview into a broken promise.
    // `grouped` spells out the narrow no-break space the readout separates
    // thousands with: an invisible literal here reads as a plain space,
    // passes review, and fails the run.
    expect(readout.textContent).toContain(grouped("3 064"));
    // 1 056, not 1 050: the view rounds the measured width to 16 px steps so a
    // one-pixel drift cannot churn the layout, and the readout reports the width
    // the arrangement was actually decided from rather than a truer-looking one.
    expect(readout.textContent).toContain(grouped("1 056"));
  });

  it("says all side by side once the window really is wide enough", async () => {
    await stageAt(3200, "8");

    const readout = screen.getByTestId("workspace-stage-readout");
    expect(readout.textContent).toContain("8 across");
    expect(readout.textContent).toContain("1 down");
    expect(readout.textContent).toMatch(/wide enough/i);
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
    vi.mocked(api.fetchIdeState).mockResolvedValue(
      stateWith(sessionWith(["Mika"])),
    );
    render(<AgenticIdeView />);
    expect(await screen.findByTestId("pane-Mika")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /next/i })).toBeNull();
  });

  it("toggles focus mode through the API, not just locally", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue(
      stateWith(sessionWith(["Mika"])),
    );
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
    await waitFor(() =>
      expect(api.setFocusMode).toHaveBeenLastCalledWith(!before),
    );
    await waitFor(() =>
      expect(toggle.getAttribute("aria-pressed")).toBe(String(!before)),
    );
  });

  it("sends a prompt to the selected terminal through the same endpoint voice uses", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue(
      stateWith(sessionWith(["Mika", "Nova"])),
    );
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
    vi.mocked(api.fetchIdeState).mockResolvedValue(
      stateWith(sessionWith(["Mika"])),
    );
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
      expect(pushToast).toHaveBeenCalledWith(
        "error",
        "Mika is not running right now",
      ),
    );
  });
  it("shows panes that voice opened, without a reload", async () => {
    /*
     * The regression this guards: a spoken "spawn three more terminals" adds the
     * panes in the backend, but this view fetches its state once on mount. Before
     * the listener existed, the agents were running and the user saw the old grid
     * — the feature looked broken while working perfectly.
     */
    vi.mocked(api.fetchIdeState).mockResolvedValue(
      stateWith(sessionWith(["Mika", "Nova"])),
    );
    render(<AgenticIdeView />);
    await screen.findByTestId("pane-Mika");
    expect(screen.queryByTestId("pane-Aria")).toBeNull();

    // Voice opened a third pane; the WebSocket layer turns the bus event into
    // this window event.
    vi.mocked(api.fetchIdeState).mockResolvedValue(
      stateWith(sessionWith(["Mika", "Nova", "Aria"])),
    );
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

describe("AgenticIdeView — resuming the last workspace", () => {
  it("offers the previous workspace above the wizard", async () => {
    vi.mocked(api.fetchResumeOffer).mockResolvedValue(PREVIOUS_WORKSPACE);
    render(<AgenticIdeView />);

    await screen.findByTestId("resume-card");
    expect(screen.getByTestId("resume-pane-alex")).toBeTruthy();
    // The wizard is still right there — the offer never blocks it.
    expect(screen.getByText("Folder")).toBeTruthy();
  });

  it("says nothing when there is nothing to resume", async () => {
    render(<AgenticIdeView />);
    await screen.findByText("Folder");
    expect(screen.queryByTestId("resume-card")).toBeNull();
  });

  it("does not offer a resume while a workspace is open", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue(
      stateWith(sessionWith(["Mika"])),
    );
    vi.mocked(api.fetchResumeOffer).mockResolvedValue(PREVIOUS_WORKSPACE);
    render(<AgenticIdeView />);

    await screen.findByTestId("pane-Mika");
    expect(screen.queryByTestId("resume-card")).toBeNull();
  });

  it("reopens the workspace and reports what really came back", async () => {
    vi.mocked(api.fetchResumeOffer).mockResolvedValue(PREVIOUS_WORKSPACE);
    vi.mocked(api.resumeWorkspace).mockResolvedValue({
      state: stateWith(sessionWith(["Mika", "Nova"])),
      workspace_count: 1,
      terminal_count: 2,
      resumable_count: 1,
      started_fresh: 1,
      skipped: [],
    });
    render(<AgenticIdeView />);

    fireEvent.click(await screen.findByTestId("resume-all"));

    await screen.findByTestId("pane-Mika");
    expect(screen.queryByTestId("resume-card")).toBeNull();
    // The honest report: one pane came back empty and the user is told so.
    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith(
        "success",
        expect.stringMatching(/1 continued, 1 started fresh/),
      ),
    );
  });

  it("forgets the workspace when the user starts fresh", async () => {
    vi.mocked(api.fetchResumeOffer).mockResolvedValue(PREVIOUS_WORKSPACE);
    vi.mocked(api.forgetResumeOffer).mockResolvedValue(undefined);
    render(<AgenticIdeView />);

    fireEvent.click(await screen.findByTestId("resume-dismiss"));

    await waitFor(() => expect(api.forgetResumeOffer).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("resume-card")).toBeNull();
  });
});

/*
 * The workspace bar.
 *
 * What these defend is the promise the bar makes by existing: several
 * workspaces are open at once, and moving between them costs nothing. The
 * ordering assertions are the important ones — the backend has to be told the
 * front workspace changed BEFORE the outgoing panes unmount, because a pane
 * that disappears while its workspace is still the front one is indistinguishable
 * from a close.
 */
function twoWorkspaces(): api.IdeState {
  const front = sessionWith(["Mika"]);
  const base = stateWith(front);
  return {
    ...base,
    workspaces: [
      {
        id: "ide_other",
        folder: "/work/api",
        name: "api",
        branch: "main",
        terminals: 3,
        live_terminals: 2,
        focus_mode: false,
        created_at: 0,
        last_active_at: 0,
        active: false,
      },
      ...base.workspaces,
    ],
  };
}

describe("AgenticIdeView — the workspace bar", () => {
  it("lists every open workspace and marks the one on screen", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue(twoWorkspaces());
    render(<AgenticIdeView />);

    await screen.findByTestId("workspace-bar");
    const other = screen.getByTestId("workspace-tab-ide_other");
    const front = screen.getByTestId("workspace-tab-ide_test");
    expect(other.getAttribute("aria-selected")).toBe("false");
    expect(front.getAttribute("aria-selected")).toBe("true");
    // The badge is one number: open terminal panes, not running agents versus
    // every pane ever placed in the workspace.
    expect(screen.getByTestId("workspace-panes-ide_other").textContent).toBe(
      "3",
    );
  });

  it("uses the active session count instead of a stale spawn total", async () => {
    const active = sessionWith(["Mika", "Nova"]);
    const state = stateWith(active);
    state.workspaces[0] = {
      ...state.workspaces[0],
      terminals: 60,
      live_terminals: 60,
    };
    vi.mocked(api.fetchIdeState).mockResolvedValue(state);
    render(<AgenticIdeView />);

    expect(
      (await screen.findByTestId("workspace-panes-ide_test")).textContent,
    ).toBe("2");
  });

  it("stays hidden while nothing is open", async () => {
    render(<AgenticIdeView />);
    await screen.findByText("Folder");
    expect(screen.queryByTestId("workspace-bar")).toBeNull();
  });

  it("switches to another workspace through the API", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue(twoWorkspaces());
    const other = sessionWith(["Kai"]);
    other.id = "ide_other";
    vi.mocked(api.activateWorkspace).mockResolvedValue(stateWith(other));
    render(<AgenticIdeView />);

    fireEvent.click(await screen.findByTestId("workspace-tab-ide_other"));

    await waitFor(() =>
      expect(api.activateWorkspace).toHaveBeenCalledWith("ide_other"),
    );
    await screen.findByTestId("pane-Kai");
    // Switching is not closing: nothing was ended.
    expect(api.endIdeSession).not.toHaveBeenCalled();
    expect(api.closeWorkspace).not.toHaveBeenCalled();
  });

  it("clears the front workspace before showing the wizard for a new one", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue(twoWorkspaces());
    vi.mocked(api.activateWorkspace).mockResolvedValue({
      ...twoWorkspaces(),
      session: null,
      active_id: null,
      active: false,
    });
    render(<AgenticIdeView />);

    fireEvent.click(await screen.findByTestId("workspace-add"));

    // null, not a close: the workspaces stay open with their agents running.
    await waitFor(() =>
      expect(api.activateWorkspace).toHaveBeenCalledWith(null),
    );
    expect(api.closeWorkspace).not.toHaveBeenCalled();
    expect(api.endIdeSession).not.toHaveBeenCalled();
    // The wizard is showing, and the bar still lists both workspaces.
    await screen.findByText("Folder");
    expect(screen.getByTestId("workspace-tab-ide_other")).toBeTruthy();
    expect(screen.getByTestId("workspace-tab-ide_test")).toBeTruthy();
  });

  it("asks before closing a workspace, then closes only that one", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue(twoWorkspaces());
    const left = stateWith(sessionWith(["Mika"]));
    vi.mocked(api.closeWorkspace).mockResolvedValue(left);
    render(<AgenticIdeView />);

    fireEvent.click(await screen.findByTestId("workspace-close-ide_other"));
    // One click arms it; the workspace is still open at this point.
    expect(api.closeWorkspace).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("workspace-close-confirm-ide_other"));
    await waitFor(() =>
      expect(api.closeWorkspace).toHaveBeenCalledWith("ide_other"),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("workspace-tab-ide_other")).toBeNull(),
    );
  });

  it("renames a workspace from the pencil action", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue(twoWorkspaces());
    const renamed = twoWorkspaces();
    renamed.workspaces = renamed.workspaces.map((workspace) =>
      workspace.id === "ide_other"
        ? { ...workspace, name: "Backend review" }
        : workspace,
    );
    vi.mocked(api.renameWorkspace).mockResolvedValue(renamed);
    render(<AgenticIdeView />);

    fireEvent.click(await screen.findByTestId("workspace-rename-ide_other"));
    const input = screen.getByTestId(
      "workspace-rename-input-ide_other",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Backend review" } });
    fireEvent.click(screen.getByTestId("workspace-rename-save-ide_other"));

    await waitFor(() =>
      expect(api.renameWorkspace).toHaveBeenCalledWith(
        "ide_other",
        "Backend review",
      ),
    );
    expect(await screen.findByText("Backend review")).toBeTruthy();
    expect(api.closeWorkspace).not.toHaveBeenCalled();
  });

  it("refuses to add one past the cap", async () => {
    const full = twoWorkspaces();
    vi.mocked(api.fetchIdeState).mockResolvedValue({
      ...full,
      max_workspaces: 2,
    });
    render(<AgenticIdeView />);

    const add = (await screen.findByTestId(
      "workspace-add",
    )) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
  });
});
