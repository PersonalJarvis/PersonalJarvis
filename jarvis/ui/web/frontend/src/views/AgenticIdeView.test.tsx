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

// Most component-specific copy remains an identity translation in this suite.
// The launcher navigation uses real English labels so its accessible workflow
// stays readable while the locale dictionaries are tested separately.
const launcherEnglish = vi.hoisted<Record<string, string>>(() => ({
  "workspace_launcher.wizard.steps.folder.title": "Choose the project folder",
  "workspace_launcher.wizard.steps.layout.title": "Shape the workspace",
  "workspace_launcher.wizard.steps.view.title": "Choose how to read the workspace",
  "workspace_launcher.wizard.steps.review.title": "Review before opening",
  "workspace_launcher.wizard.continue_layout": "Continue to layout",
  "workspace_launcher.wizard.continue_agents": "Continue to agents",
  "workspace_launcher.wizard.choose_view": "Choose the view",
  "workspace_launcher.wizard.review_workspace": "Review workspace",
  "workspace_launcher.wizard.open_workspace": "Open workspace",
  "workspace_launcher.wizard.terminal_unavailable_before":
    "This machine has no usable terminal backend. Required:",
  "workspace_launcher.wizard.no_cli":
    "No coding-agent CLI was found on this machine's PATH.",
  "workspace_launcher.wizard.open_clis": "Open CLIs",
  "workspace_launcher.wizard.views.grid.title": "Terminal grid",
  "workspace_launcher.wizard.views.chat.title": "Chat view",
}));
vi.mock("@/i18n", () => ({
  useT: () => (key: string) => launcherEnglish[key] ?? key,
}));

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

/*
 * The IDE carries the app's own Restart/Update actions in its header row, so
 * this view now mounts the updater hook — which fetches `/api/update/status`
 * the moment it appears. jsdom has no server behind that, so left real it is an
 * unhandled request on every render in this file, and the button's visibility
 * would depend on how that failure happened to land. "No update offered" is
 * both the deterministic answer and the state of every dev tree.
 */
vi.mock("@/hooks/useUpdate", () => ({
  useUpdate: () => ({
    status: { managed: false, update_available: false },
    refresh: vi.fn(),
  }),
}));

// xterm.js needs a real canvas, which jsdom has not got — stub the panes.
vi.mock("@/components/agentic/AgenticTerminal", () => ({
  AgenticTerminal: ({ name }: { name: string }) => (
    <div data-testid={`pane-${name}`}>{name}</div>
  ),
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
  syncAgenticIdeSurface: vi.fn(async () => undefined),
  fetchAllVoiceAttachments: vi.fn(async () => ({ batches: [] })),
  attachToTerminal: vi.fn(),
  removeVoiceAttachment: vi.fn(async () => undefined),
  // The grid polls this to keep the pane headers current.
  fetchTerminalRecaps: vi.fn(async () => ({
    workspace_id: null,
    terminals: [],
  })),
  // The remembered terminal text size, read once when the grid mounts.
  fetchTerminalUiPreferences: vi.fn(async () => ({
    terminal_font_size: 13,
    stored: false,
    min: 10,
    max: 20,
    default: 13,
  })),
  saveTerminalFontSize: vi.fn(async (size: number) => ({
    terminal_font_size: size,
    stored: true,
    min: 10,
    max: 20,
    default: 13,
  })),
}));

import { AgenticIdeView } from "./AgenticIdeView";
import { workspaceLaunchShortcut } from "@/components/agentic/WorkspaceLauncher";
import * as api from "@/lib/agenticIdeApi";
import { GRID_HORIZONTAL_PADDING_PX } from "@/components/agentic/layout";

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
      name: "",
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
  // A workspace now opens with its prompt bar collapsed — the panes are what
  // the user came for. The tests below that type an instruction want it open,
  // and the remembered height is how a user who wants it open gets it.
  window.localStorage.setItem("jarvis.agenticIde.composerHeight.v2", "176");
  // The wizard's view step preselects the remembered reading mode, and the
  // grid reads the same key on mount — a value left behind by one test must
  // not decide how the next one's workspace opens.
  window.localStorage.removeItem("jarvis.agenticIde.workspaceView");
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

describe("Agentic IDE — before the first read lands", () => {
  /**
   * A workspace the view has not heard about yet is NOT "no workspace".
   *
   * This view used to fall straight through to the launcher while it waited, so
   * a user returning to a running workspace was met by step 1 of the onboarding
   * flow asking which folder the agents should work in — in front of eleven
   * agents that had never stopped (maintainer report 2026-07-29). Being told to
   * wait a moment is a far better answer than being told to start over.
   */
  it("shows a neutral placeholder rather than the onboarding screen", async () => {
    // Both reads hang: this is the state the view is in on every cold start,
    // held open so it can be asserted on.
    vi.mocked(api.fetchIdeState).mockReturnValue(new Promise(() => {}));
    vi.mocked(api.fetchIdeAgents).mockReturnValue(new Promise(() => {}));

    render(<AgenticIdeView />);

    expect(await screen.findByTestId("agentic-ide-loading")).toBeTruthy();
    // The thing that would read as "there is nothing open here".
    expect(screen.queryByTestId("workspace-launcher")).toBeNull();
  });

  it("draws a running workspace without waiting for the CLI sweep", async () => {
    // `/agents` starts one subprocess per registered CLI and takes over a
    // second on a cold cache. The grid never asks what is installed, so it must
    // not wait for the answer — this is the other half of the 3 s on the way
    // back into the section.
    vi.mocked(api.fetchIdeAgents).mockReturnValue(new Promise(() => {}));
    vi.mocked(api.fetchIdeState).mockResolvedValue(
      stateWith(sessionWith(["Mika", "Nova"])),
    );

    render(<AgenticIdeView />);

    expect(await screen.findByTestId("pane-Mika")).toBeTruthy();
  });

  it("holds the placeholder until the launcher has agents to offer", async () => {
    // The reverse: with no workspace open, the wizard's agent step is the whole
    // point of the sweep, so showing the wizard before it lands would offer an
    // empty list.
    vi.mocked(api.fetchIdeAgents).mockReturnValue(new Promise(() => {}));

    render(<AgenticIdeView />);

    expect(await screen.findByTestId("agentic-ide-loading")).toBeTruthy();
    expect(screen.queryByTestId("workspace-launcher")).toBeNull();
  });
});

describe("Agentic IDE launcher", () => {
  it("shows the launch chord for the current operating system", () => {
    expect(workspaceLaunchShortcut("Win32")).toBe("Ctrl+↵");
    expect(workspaceLaunchShortcut("MacIntel")).toBe("⌘↵");
    expect(workspaceLaunchShortcut("Linux x86_64")).toBe("Ctrl+↵");
  });

  async function chooseFolder(): Promise<void> {
    fireEvent.click(await screen.findByRole("button", { name: /project/i }));
  }

  async function openLayout(): Promise<void> {
    await chooseFolder();
    fireEvent.click(
      screen.getByRole("button", { name: /continue to layout/i }),
    );
  }

  async function openAgents(): Promise<void> {
    await openLayout();
    fireEvent.click(
      screen.getByRole("button", { name: /continue to agents/i }),
    );
  }

  it("keeps later decisions locked until a folder is picked", async () => {
    render(<AgenticIdeView />);
    await waitFor(() => expect(api.fetchIdeAgents).toHaveBeenCalled());

    const next = screen.getByRole("button", {
      name: /continue to layout/i,
    }) as HTMLButtonElement;
    expect(next.disabled).toBe(true);
    expect(
      (screen.getByTestId("launcher-step-layout") as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    await chooseFolder();
    await waitFor(() => expect(next.disabled).toBe(false));
  });

  it("walks through folder, layout, agents, view and review before opening", async () => {
    vi.mocked(api.startIdeSession).mockResolvedValue(
      stateWith(sessionWith(["Mika", "Nova"])),
    );
    render(<AgenticIdeView />);
    await waitFor(() => expect(api.fetchIdeAgents).toHaveBeenCalled());

    expect(
      screen.getByRole("heading", { name: /choose the project folder/i }),
    ).toBeTruthy();
    expect(screen.queryByDisplayValue("Mika")).toBeNull();

    await openLayout();
    expect(
      screen.getByRole("heading", { name: /shape the workspace/i }),
    ).toBeTruthy();
    expect(
      screen.getByRole("textbox", { name: /number of terminals/i }),
    ).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: /continue to agents/i }),
    );
    expect(await screen.findByText("0 / 2")).toBeTruthy();
    fireEvent.click(screen.getAllByText("workspace_launcher.agents.all")[0]);
    expect(screen.getByText("2 / 2")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /choose the view/i }));
    expect(
      screen.getByRole("heading", { name: /choose how to read the workspace/i }),
    ).toBeTruthy();
    // The full terminal grid is the preselected answer, not an open question.
    expect(
      screen.getByTestId("view-choice-grid").getAttribute("aria-checked"),
    ).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: /review workspace/i }));
    expect(
      screen.getByRole("heading", { name: /review before opening/i }),
    ).toBeTruthy();
    expect(screen.getByTestId("review-view-mode").textContent).toBe(
      "Terminal grid",
    );
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

  it("opens the workspace in chat view when the view step says so", async () => {
    vi.mocked(api.startIdeSession).mockResolvedValue(
      stateWith(sessionWith(["Mika", "Nova"])),
    );
    render(<AgenticIdeView />);
    await waitFor(() => expect(api.fetchIdeAgents).toHaveBeenCalled());
    await openAgents();
    fireEvent.click(screen.getAllByText("workspace_launcher.agents.all")[0]);

    fireEvent.click(screen.getByRole("button", { name: /choose the view/i }));
    fireEvent.click(screen.getByTestId("view-choice-chat"));
    expect(
      screen.getByTestId("view-choice-chat").getAttribute("aria-checked"),
    ).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: /review workspace/i }));
    expect(screen.getByTestId("review-view-mode").textContent).toBe(
      "Chat view",
    );
    fireEvent.click(screen.getByRole("button", { name: /open workspace/i }));

    // The grid reads the stored preference on mount, so the workspace comes up
    // with the chat rail showing instead of the wall of terminals.
    expect(await screen.findByTestId("pane-Mika")).toBeTruthy();
    expect(
      window.localStorage.getItem("jarvis.agenticIde.workspaceView"),
    ).toBe("chat");
    const rail = screen.getByTestId("agentic-chat-rail");
    expect(rail.className).toContain("flex");
    expect(rail.className).not.toContain("hidden");
  });

  it("keeps every reading-mode miniature visible on the dark workspace", async () => {
    render(<AgenticIdeView />);
    await waitFor(() => expect(api.fetchIdeAgents).toHaveBeenCalled());
    await openAgents();
    fireEvent.click(screen.getAllByText("workspace_launcher.agents.all")[0]);
    fireEvent.click(screen.getByRole("button", { name: /choose the view/i }));

    for (const view of ["grid", "chat"] as const) {
      const panes = screen
        .getByTestId(`view-choice-${view}`)
        .querySelectorAll("[data-view-preview-pane]");
      expect(panes).toHaveLength(4);
      for (const pane of panes) {
        expect(pane.className).toContain("border-foreground/20");
        expect(pane.className).toContain("bg-foreground/");
      }
    }
  });

  it("keeps an aggregate agent split across backward navigation", async () => {
    render(<AgenticIdeView />);
    await waitFor(() => expect(api.fetchIdeAgents).toHaveBeenCalled());
    await openAgents();

    fireEvent.click(screen.getByText("workspace_launcher.agents.split_evenly"));
    fireEvent.click(screen.getByTestId("launcher-step-folder"));

    const path = screen.getByTestId("folder-path-input");
    fireEvent.change(path, { target: { value: "/work/another" } });
    fireEvent.keyDown(path, { key: "Enter" });

    fireEvent.click(screen.getByTestId("launcher-step-agents"));

    expect(
      screen
        .getAllByRole("spinbutton")
        .map((input) => (input as HTMLInputElement).value),
    ).toEqual(["1", "1"]);
  });

  /**
   * Set the count to ``n`` in a workspace of ``width`` × ``height`` pixels and
   * return the stage — the miniature of the workspace that is about to open.
   */
  async function stageAt(
    width: number,
    n: string,
    height: number = 0,
  ): Promise<HTMLElement> {
    const previous = globalThis.ResizeObserver;
    class WidthObserver {
      constructor(private readonly callback: ResizeObserverCallback) {}
      observe(): void {
        this.callback(
          [{ contentRect: { width, height } } as ResizeObserverEntry],
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
      await openLayout();
      fireEvent.change(
        await screen.findByRole("textbox", {
          name: /number of terminals/i,
        }),
        { target: { value: n } },
      );
      return screen.getByTestId("workspace-stage-grid");
    } finally {
      globalThis.ResizeObserver = previous;
    }
  }

  /** Columns and rows the stage lays its panes out in. */
  const columnsOf = (stage: HTMLElement) => stage.style.gridTemplateColumns;
  const rowsOf = (stage: HTMLElement) => stage.style.gridTemplateRows;
  /**
   * The width the stage draws the workspace at — always its own frame.
   *
   * It used to be set inline to more than 100 % when the workspace was wider
   * than the window, which was the honest preview of a grid you scrolled
   * sideways. Nothing scrolls now (maintainer, 2026-08-04), so an inline width
   * appearing here at all would mean the preview is promising a workspace the
   * running grid will not build.
   */
  const widthOf = (stage: HTMLElement) => stage.style.width;

  it("sets any count from one control instead of cards plus a custom row", async () => {
    // Two ways to set one number meant two competing "selected" states. One
    // control now covers every value, and its two grips must never disagree.
    await stageAt(2328, "10");

    expect(
      (
        screen.getByRole("slider", {
          name: /number of terminals/i,
        }) as HTMLInputElement
      ).value,
    ).toBe("10");
    expect(
      (
        screen.getByRole("textbox", {
          name: /number of terminals/i,
        }) as HTMLInputElement
      ).value,
    ).toBe("10");
    expect(
      screen.queryByRole("button", { name: /custom terminals/i }),
    ).toBeNull();
  });

  it("lets the exact terminal count be cleared and typed directly", async () => {
    render(<AgenticIdeView />);
    await waitFor(() => expect(api.fetchIdeAgents).toHaveBeenCalled());
    await openLayout();

    const input = await screen.findByRole("textbox", {
      name: /number of terminals/i,
    });
    fireEvent.change(input, { target: { value: "" } });
    expect((input as HTMLInputElement).value).toBe("");

    fireEvent.change(input, { target: { value: "11" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect((input as HTMLInputElement).value).toBe("11");
    expect(
      (
        screen.getByRole("slider", {
          name: /number of terminals/i,
        }) as HTMLInputElement
      ).value,
    ).toBe("11");
  });

  it("builds the requested number of terminal plans", async () => {
    render(<AgenticIdeView />);
    await waitFor(() => expect(api.fetchIdeAgents).toHaveBeenCalled());
    await openLayout();
    fireEvent.change(
      await screen.findByRole("textbox", { name: /number of terminals/i }),
      { target: { value: "10" } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: /continue to agents/i }),
    );

    expect(screen.getByText("0 / 10")).toBeTruthy();
    expect(
      screen
        .getByRole("progressbar", {
          name: "workspace_launcher.agents.assigned",
        })
        .getAttribute("aria-valuemax"),
    ).toBe("10");
  });

  it("caps the count at the backend limit", async () => {
    await stageAt(2328, "99");

    expect(
      (
        screen.getByRole("textbox", {
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

  it("previews 12 terminals as six columns of two, all of them on one screen", async () => {
    // One screenful, opened two deep: a single line spent the whole window on
    // one row, and past five panes each one fell under the width a 60-column
    // agent grid needs, clipping at the tile edge (reported 2026-08-11 —
    // WIZARD_COLUMN_HEIGHT in layout.ts holds the full rationale). The preview
    // mirrors the exact panes the backend opens: columns of two.
    const stage = await stageAt(2328, "12");
    expect(columnsOf(stage)).toBe("repeat(6, minmax(0, 1fr))");
    expect(rowsOf(stage)).toBe("repeat(2, minmax(0, 1fr))");
    expect(widthOf(stage)).toBe("");
  });

  it("previews the same twelve in a narrow window, still all on one screen", async () => {
    // Neither the arrangement nor how much of it you see depends on the window
    // any more — only how wide each pane ends up, which the readout says.
    const stage = await stageAt(1314, "12");
    expect(columnsOf(stage)).toBe("repeat(6, minmax(0, 1fr))");
    expect(rowsOf(stage)).toBe("repeat(2, minmax(0, 1fr))");
    expect(widthOf(stage)).toBe("");
  });

  it("keeps a small workspace in wizard columns at any usable width", async () => {
    // Three panes are a full column of two plus one beside it — the second
    // column's single pane spans the height, so the grid stays two rows.
    const stage = await stageAt(1314, "3");
    expect(columnsOf(stage)).toBe("repeat(2, minmax(0, 1fr))");
    expect(rowsOf(stage)).toBe("repeat(2, minmax(0, 1fr))");
    // Nothing to scroll past, so the stage is an ordinary full-width grid.
    expect(widthOf(stage)).toBe("");
  });

  it("previews four terminals as two columns of two in a 2K workspace", async () => {
    // The wizard's opening shape is columns of two (layout.ts,
    // WIZARD_COLUMN_HEIGHT): four terminals start as a 2 x 2. Only the OPENING
    // shape — the user's own splits and drags rearrange it freely afterwards.
    const stage = await stageAt(2048, "4", 1164);
    expect(columnsOf(stage)).toBe("repeat(2, minmax(0, 1fr))");
    expect(rowsOf(stage)).toBe("repeat(2, minmax(0, 1fr))");
    expect(widthOf(stage)).toBe("");
  });

  it("draws one pane per terminal and never grows the box to fit them", async () => {
    // The old dot preview sat in a fixed 40×40 px box with nothing bounding it,
    // so a high count in a narrow window grew a tall column of dots straight out
    // through the card, over the buttons above and below. The stage is a FIXED
    // frame, and every pane is drawn inside it however many there are.
    const stage = await stageAt(800, "12");
    expect(stage.children.length).toBe(12);
    expect(columnsOf(stage)).toBe("repeat(6, minmax(0, 1fr))");
    expect(rowsOf(stage)).toBe("repeat(2, minmax(0, 1fr))");
    expect(widthOf(stage)).toBe("");
  });

  it("names the width each pane ends up with, and warns when that is tight", async () => {
    // The reported bug behind this readout: an arrangement stated without its
    // consequence. The consequence changed on 2026-08-04 — nothing scrolls, so
    // a high count is paid for in pane WIDTH — and the sentence says that.
    await stageAt(1050, "8");

    const readout = screen.getByTestId("workspace-stage-readout");
    // Eight terminals open as four columns of two (layout.ts,
    // WIZARD_COLUMN_HEIGHT), so the width is shared four ways, not eight.
    expect(readout.textContent).toContain("4 across");
    expect(readout.textContent).toContain("All on one screen");
    // Never a promise of somewhere else to look: every pane is on this screen.
    expect(readout.textContent).not.toMatch(/scroll/i);
    // 1 056, not 1 050: the view rounds the measured width to 16 px steps, and
    // 8 panes share it minus the grid's own padding. Computed rather than
    // written out, because tightening that padding moves the number without
    // changing anything the test is about.
    const each = Math.round((1056 - GRID_HORIZONTAL_PADDING_PX) / 4);
    expect(readout.textContent).toContain(`${each} px each`);
    expect(readout.textContent).toMatch(/narrow for an agent/i);
  });

  it("drops the warning once the panes really are roomy", async () => {
    await stageAt(3200, "8");

    const readout = screen.getByTestId("workspace-stage-readout");
    expect(readout.textContent).toContain("4 across");
    expect(readout.textContent).toContain("All on one screen");
    expect(readout.textContent).not.toMatch(/narrow for an agent/i);
    expect(readout.textContent).not.toMatch(/scroll/i);
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
    expect(screen.queryByTestId("workspace-launcher")).toBeNull();
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

  /*
   * The mode is scoped to this section, and the signal is `onScreen`.
   *
   * This view is sticky — MainView keeps it mounted so a workspace survives a
   * trip to Settings — so there is no unmount to clean up on. That is exactly
   * how the mode came to be permanently on: this view switched it on and
   * nothing ever switched it off, leaving the assistant in coding mode on every
   * screen for the rest of the session.
   */
  it("hands an auto-enabled coding mode back when the section is left", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue(
      stateWith(sessionWith(["Mika"])),
    );
    vi.mocked(api.setFocusMode).mockResolvedValue(true);
    const { rerender } = render(<AgenticIdeView onScreen />);

    await waitFor(() => expect(api.setFocusMode).toHaveBeenCalledWith(true));

    vi.mocked(api.setFocusMode).mockResolvedValue(false);
    rerender(<AgenticIdeView onScreen={false} />);
    await waitFor(() => expect(api.setFocusMode).toHaveBeenLastCalledWith(false));
  });

  it("switches the mode back on when the section is returned to", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue(
      stateWith(sessionWith(["Mika"])),
    );
    vi.mocked(api.setFocusMode).mockResolvedValue(true);
    const { rerender } = render(<AgenticIdeView onScreen />);
    await waitFor(() => expect(api.setFocusMode).toHaveBeenCalledWith(true));

    vi.mocked(api.setFocusMode).mockResolvedValue(false);
    rerender(<AgenticIdeView onScreen={false} />);
    await waitFor(() => expect(api.setFocusMode).toHaveBeenLastCalledWith(false));

    // The cached session still claims focus_mode is on, which is precisely why
    // the auto-enable gate reads the local flag instead of the session.
    vi.mocked(api.setFocusMode).mockResolvedValue(true);
    rerender(<AgenticIdeView onScreen />);
    await waitFor(() => expect(api.setFocusMode).toHaveBeenLastCalledWith(true));
  });

  it("leaves a hand-toggled coding mode alone when the section is left", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue(
      stateWith(sessionWith(["Mika"], true)),
    );
    vi.mocked(api.setFocusMode).mockResolvedValue(true);
    const { rerender } = render(<AgenticIdeView onScreen />);

    const toggle = await screen.findByTestId("agentic-focus-toggle");
    // Off by hand, then on by hand: now the mode is the user's, not the screen's,
    // so walking away must not take it back. This is what keeps "ask Jarvis
    // about my terminals from another room" working.
    vi.mocked(api.setFocusMode).mockResolvedValue(false);
    fireEvent.click(toggle);
    await waitFor(() => expect(api.setFocusMode).toHaveBeenLastCalledWith(false));
    vi.mocked(api.setFocusMode).mockResolvedValue(true);
    fireEvent.click(toggle);
    await waitFor(() => expect(api.setFocusMode).toHaveBeenLastCalledWith(true));

    const callsBeforeLeaving = vi.mocked(api.setFocusMode).mock.calls.length;
    rerender(<AgenticIdeView onScreen={false} />);
    await Promise.resolve();
    expect(vi.mocked(api.setFocusMode).mock.calls.length).toBe(
      callsBeforeLeaving,
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
    const box = screen.getByLabelText(/instruction for mika/i);
    fireEvent.change(box, { target: { value: "run the tests" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() =>
      // The third argument carries files dropped on the prompt bar — empty
      // here, because this instruction was typed with nothing attached.
      expect(api.promptTerminal).toHaveBeenCalledWith("Mika", "run the tests", {
        attachments: [],
      }),
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
    fireEvent.change(screen.getByLabelText(/instruction for/i), {
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
    // The launcher is still right there — the offer never blocks it.
    expect(screen.getByTestId("workspace-launcher")).toBeTruthy();
  });

  it("says nothing when there is nothing to resume", async () => {
    render(<AgenticIdeView />);
    await screen.findByTestId("workspace-launcher");
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
    await screen.findByTestId("workspace-launcher");
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
    // The launcher is showing, and the bar still lists both workspaces.
    await screen.findByTestId("workspace-launcher");
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

  it("keeps adding available when the backend has no workspace cap", async () => {
    vi.mocked(api.fetchIdeState).mockResolvedValue({
      ...twoWorkspaces(),
      max_workspaces: null,
    });
    render(<AgenticIdeView />);

    const add = (await screen.findByTestId(
      "workspace-add",
    )) as HTMLButtonElement;
    expect(add.disabled).toBe(false);
  });
});
