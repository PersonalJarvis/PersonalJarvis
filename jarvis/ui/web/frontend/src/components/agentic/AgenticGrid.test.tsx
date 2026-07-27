import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// jsdom has no layout observer. The grid only needs the callback in a real
// browser; a no-op keeps its initial layout deterministic in component tests.
class ResizeObserverPolyfill {
  constructor(_callback: ResizeObserverCallback) {}
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = ResizeObserverPolyfill;
}

const pushToast = vi.fn();
vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ pushToast }),
}));

vi.mock("@/lib/agenticIdeApi", () => ({
  addTerminal: vi.fn(),
  attachToTerminal: vi.fn(),
  closeTerminal: vi.fn(),
  closeTerminals: vi.fn(),
  composePrompt: vi.fn(),
  // Polled by the grid so the pane headers keep saying what their agents are
  // doing. Resolves empty by default; the recap tests give it real rows.
  fetchTerminalRecaps: vi.fn(async () => ({
    workspace_id: "ide_test",
    terminals: [],
  })),
  promptTerminal: vi.fn(),
  // Reached from the toolbar's settings panel, which the grid always renders.
  setIdeActiveAccount: vi.fn(),
  // Polled by the toolbar's "Continue" control, which the grid also always
  // renders. Answers "nothing was interrupted" so these tests see the ordinary
  // toolbar rather than a badge none of them are about.
  fetchInterrupted: vi.fn(async () => ({
    count: 0,
    continuable_count: 0,
    prompt: "continue",
    panes: [],
  })),
  continueInterrupted: vi.fn(),
}));

// The grid follows the app theme for its terminal colours; these tests render
// it outside the provider, so the hook is stubbed rather than the whole app.
vi.mock("@/hooks/useTheme", () => ({
  useThemeValue: () => "dark",
}));

/**
 * xterm needs a real canvas, so the pane is stubbed — but the stub exposes the
 * same action buttons, because what these tests check is the WIRING: which call
 * a button makes and what the grid does with the answer.
 */
vi.mock("./AgenticTerminal", () => ({
  AgenticTerminal: ({
    name,
    maximized,
    splitDisabled,
    restartToken,
    recap,
    recapDetail,
    onRestart,
    agents,
    onToggleMaximize,
    onSplit,
    onClose,
  }: {
    name: string;
    maximized?: boolean;
    splitDisabled?: boolean;
    restartToken?: number;
    recap?: string;
    recapDetail?: string;
    onRestart?: () => void;
    agents?: Array<{ name: string }>;
    onToggleMaximize?: () => void;
    onSplit?: (direction: "right" | "down", agent?: string) => void;
    onClose?: () => void;
  }) => (
    <div
      data-testid={`pane-${name}`}
      data-maximized={maximized ? "yes" : "no"}
      data-agents={(agents ?? []).map((a) => a.name).join(",")}
      data-restart-token={String(restartToken ?? 0)}
      data-recap={recap ?? ""}
      data-recap-detail={recapDetail ?? ""}
    >
      {name}
      <button data-testid={`pane-maximize-${name}`} onClick={onToggleMaximize}>
        max
      </button>
      <button
        data-testid={`pane-split-right-${name}`}
        disabled={splitDisabled}
        onClick={() => onSplit?.("right")}
      >
        right
      </button>
      <button
        data-testid={`pane-split-down-${name}`}
        disabled={splitDisabled}
        onClick={() => onSplit?.("down")}
      >
        down
      </button>
      {/* The pane's own CLI picker lives in AgenticTerminal; here it stands for
          "the user chose a specific agent for this split". */}
      <button
        data-testid={`pane-split-down-codex-${name}`}
        disabled={splitDisabled}
        onClick={() => onSplit?.("down", "codex")}
      >
        down as codex
      </button>
      <button data-testid={`pane-close-${name}`} onClick={onClose}>
        close
      </button>
      <button data-testid={`pane-restart-${name}`} onClick={onRestart}>
        restart
      </button>
    </div>
  ),
  PaneStatusPill: () => <span>live</span>,
}));

import { AgenticGrid } from "./AgenticGrid";
import * as api from "@/lib/agenticIdeApi";
import type { SessionState, TerminalState } from "@/lib/agenticIdeApi";

/** One pane at (column, slot) — the workspace is columns of stacked panes. */
function pane(name: string, column: number, slot: number, index: number): TerminalState {
  return {
    key: name.toLowerCase(),
    name,
    agent: "claude",
    display_name: "Claude Code",
    index,
    column,
    slot,
    status: "live",
    exit_code: null,
    error: "",
    started_at: 0,
    last_output_at: 0,
    idle_seconds: 0,
    prompts_sent: 0,
    last_prompt: "",
    lines_captured: 0,
  };
}

/** `panes` are [name, column, slot] triples; slot defaults to 0 (one row). */
function sessionWith(panes: Array<[string, number] | [string, number, number]>): SessionState {
  return {
    id: "ide_test",
    folder: "/work/project",
    project: {
      path: "/work/project",
      name: "project",
      exists: true,
      is_repo: true,
      branch: "main",
      stacks: [],
      instruction_files: [],
      top_level_dirs: [],
      skills: [],
      subagents: [],
      commands: [],
      note: "",
    },
    created_at: 0,
    focus_mode: false,
    terminals: panes.map(([name, column, slot], i) => pane(name, column, slot ?? 0, i)),
  };
}

const BASE = sessionWith([
  ["Mika", 0],
  ["Nova", 1],
]);

beforeEach(() => {
  vi.mocked(api.addTerminal).mockResolvedValue(
    sessionWith([
      ["Mika", 0],
      ["Aria", 1],
      ["Nova", 2],
    ]),
  );
  vi.mocked(api.closeTerminal).mockResolvedValue(sessionWith([["Nova", 0]]));
  vi.mocked(api.closeTerminals).mockResolvedValue({
    closed: ["Mika", "Nova"],
    failed: [],
    session: sessionWith([]),
  });
  vi.mocked(api.composePrompt).mockResolvedValue({
    composed: "## Task\nRun the tests.",
    composed_by: "llm",
    files: [],
  });
  vi.mocked(api.promptTerminal).mockResolvedValue({
    terminal: "Mika",
    sent: "## Task\nRun the tests.",
    composed_by: "raw",
    files: [],
    submitted: true,
  });
  vi.mocked(api.attachToTerminal).mockResolvedValue({
    terminal: "Mika",
    references: ["@.jarvis/drops/shot.png"],
    files: ["shot.png"],
    copied: 1,
    submitted: false,
    delivered: false,
    analysis: [
      {
        name: "shot.png",
        reference: "@.jarvis/drops/shot.png",
        kind: "image",
        detail: "A login dialog whose submit button overflows its container.",
        described_by: "vision",
        note: "",
      },
    ],
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderGrid(session = BASE, extra: Record<string, unknown> = {}) {
  const onSessionChanged = vi.fn();
  const onClose = vi.fn();
  render(
    <AgenticGrid
      session={session}
      focusMode={false}
      onToggleFocus={vi.fn()}
      onClose={onClose}
      onSessionChanged={onSessionChanged}
      {...extra}
    />,
  );
  return { onSessionChanged, onClose };
}

describe("pane actions", () => {
  it("splitting right asks for a column beside the anchor", async () => {
    const { onSessionChanged } = renderGrid();
    fireEvent.click(screen.getByTestId("pane-split-right-Mika"));
    await waitFor(() =>
      expect(api.addTerminal).toHaveBeenCalledWith({
        anchor: "Mika",
        direction: "right",
      }),
    );
    await waitFor(() => expect(onSessionChanged).toHaveBeenCalled());
  });

  it("splitting down asks to split the anchor's own column", async () => {
    renderGrid();
    fireEvent.click(screen.getByTestId("pane-split-down-Nova"));
    await waitFor(() =>
      expect(api.addTerminal).toHaveBeenCalledWith({
        anchor: "Nova",
        direction: "down",
        agent: undefined,
      }),
    );
  });

  it("passes the CLI the user picked through to the new pane", async () => {
    renderGrid();
    fireEvent.click(screen.getByTestId("pane-split-down-codex-Nova"));
    await waitFor(() =>
      expect(api.addTerminal).toHaveBeenCalledWith({
        anchor: "Nova",
        direction: "down",
        agent: "codex",
      }),
    );
  });

  it("offers every known CLI to the panes, installed or not", () => {
    // The pane disables the uninstalled ones rather than hiding them, so the
    // grid hands over the whole list.
    renderGrid(BASE, {
      agents: [
        { name: "claude", displayName: "Claude Code", installed: true },
        { name: "codex", displayName: "Codex", installed: false },
      ],
    });
    expect(screen.getByTestId("pane-Mika").getAttribute("data-agents")).toBe(
      "claude,codex",
    );
  });

  it("a new pane becomes the prompt target", async () => {
    renderGrid();
    fireEvent.click(screen.getByTestId("pane-split-right-Mika"));
    // The freshly added pane is the one the user just asked for, so the prompt
    // bar should already point at it.
    await waitFor(() =>
      expect(screen.getByPlaceholderText(/instruction for Aria/i)).toBeTruthy(),
    );
  });

  it("keeps a plain terminal out of the prompt bar's targets", () => {
    // A plain terminal is a shell prompt: Jarvis does not type into one, so
    // offering it as a target would promise a delivery that is always refused.
    const session = sessionWith([
      ["Mika", 0],
      ["Nova", 1],
    ]);
    session.terminals[1] = {
      ...session.terminals[1],
      agent: "shell",
      display_name: "Plain Terminal",
      accepts_prompts: false,
    };
    renderGrid(session);

    expect(screen.getByRole("button", { name: /^Mika/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Nova live$/ })).toBeNull();
    // The agent pane, not the shell, is what the prompt goes to.
    expect(screen.getByPlaceholderText(/instruction for Mika/i)).toBeTruthy();
  });

  it("does not make a new plain terminal the prompt target", async () => {
    const next = sessionWith([
      ["Mika", 0],
      ["Aria", 1],
      ["Nova", 2],
    ]);
    next.terminals[1] = {
      ...next.terminals[1],
      agent: "shell",
      display_name: "Plain Terminal",
      accepts_prompts: false,
    };
    vi.mocked(api.addTerminal).mockResolvedValue(next);
    renderGrid();

    fireEvent.click(screen.getByTestId("pane-split-right-Mika"));

    await waitFor(() => expect(api.addTerminal).toHaveBeenCalled());
    // The prompt bar stays pointed at the agent it was on — a shell pane that
    // stole the target would silently swallow the next instruction.
    expect(screen.getByPlaceholderText(/instruction for Mika/i)).toBeTruthy();
  });

  it("reports a refused split instead of pretending it worked", async () => {
    vi.mocked(api.addTerminal).mockRejectedValue(
      new Error("This workspace already has the maximum of 12 terminals."),
    );
    renderGrid();
    fireEvent.click(screen.getByTestId("pane-split-right-Mika"));
    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith(
        "error",
        "This workspace already has the maximum of 12 terminals.",
      ),
    );
  });

  it("disables the split buttons at the terminal limit", () => {
    renderGrid(BASE, { maxTerminals: 2 });
    expect((screen.getByTestId("pane-split-right-Mika") as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect((screen.getByTestId("pane-split-down-Mika") as HTMLButtonElement).disabled).toBe(
      true,
    );
  });
});

describe("grid layout", () => {
  /** The one grid container every pane is a direct child of. */
  function gridEl(): HTMLElement {
    const cell = screen.getAllByTestId(/^pane-cell-/)[0];
    const container = cell.parentElement;
    if (!container) throw new Error("no grid container");
    return container;
  }

  /** The inline style of a pane's cell, as written (jsdom keeps it verbatim). */
  function cellStyle(name: string): string {
    return screen.getByTestId(`pane-cell-${name}`).getAttribute("style") ?? "";
  }

  it("puts a fresh workspace side by side in one row", () => {
    renderGrid(sessionWith([["Mika", 0], ["Nova", 1], ["Aria", 2], ["Kai", 3]]));
    expect(gridEl().style.gridTemplateColumns).toBe("repeat(4, minmax(0, 1fr))");
    expect(gridEl().style.gridTemplateRows).toBe("repeat(1, minmax(240px, 1fr))");
  });

  it("a downward split takes only its OWN column, not the whole width", () => {
    // The reported bug: splitting one pane used to open a window-wide row and
    // squash every other pane to half height. Nova's column holds two panes;
    // Mika and Aria keep their full height beside it.
    renderGrid(
      sessionWith([
        ["Mika", 0, 0],
        ["Nova", 1, 0],
        ["Vega", 1, 1],
        ["Aria", 2, 0],
      ]),
    );
    expect(gridEl().style.gridTemplateColumns).toBe("repeat(3, minmax(0, 1fr))");
    expect(gridEl().style.gridTemplateRows).toBe("repeat(2, minmax(240px, 1fr))");
    expect(cellStyle("Mika")).toContain("grid-row: 1 / span 2");
    expect(cellStyle("Aria")).toContain("grid-row: 1 / span 2");
    expect(cellStyle("Nova")).toContain("grid-row: 1 / span 1");
    expect(cellStyle("Vega")).toContain("grid-row: 2 / span 1");
    // Both panes of the split share one column.
    expect(cellStyle("Nova")).toContain("grid-column: 2");
    expect(cellStyle("Vega")).toContain("grid-column: 2");
  });

  it("wraps a crowded workspace into two even bands", () => {
    // 12 columns side by side are too narrow to read anything in, so they break
    // into 6 above and 6 below.
    const panes = Array.from({ length: 12 }, (_, i) => [`T${i + 1}`, i] as [string, number]);
    renderGrid(sessionWith(panes));
    expect(gridEl().style.gridTemplateColumns).toBe("repeat(6, minmax(0, 1fr))");
    expect(cellStyle("T7")).toContain("grid-row: 2 / span 1");
    // Same parent for every pane — a pane that moves to another parent element
    // is remounted, and remounting kills the agent behind it.
    expect(screen.getByTestId("pane-cell-T12").parentElement).toBe(
      screen.getByTestId("pane-cell-T1").parentElement,
    );
  });

  it("gives a maximized pane the whole grid", () => {
    const panes = Array.from({ length: 12 }, (_, i) => [`T${i + 1}`, i] as [string, number]);
    renderGrid(sessionWith(panes));
    fireEvent.click(screen.getByTestId("pane-maximize-T3"));
    // Without this the pane would stay in its one-sixth cell while the rest of
    // the workspace is blank.
    expect(cellStyle("T3")).toContain("grid-column: 1 / -1");
    expect(cellStyle("T3")).toContain("grid-row: 1 / -1");
    // ...and the grid it now spans is ONE track filling the window, not the
    // twelve-pane template. Spanning that template made the maximized pane
    // taller than the visible area in any workspace big enough to scroll, so
    // the terminal fitted itself to rows below the clip — which is where the
    // CLI keeps its prompt box.
    expect(gridEl().style.gridTemplateRows).toBe("minmax(0, 1fr)");
    expect(gridEl().style.gridTemplateColumns).toBe("minmax(0, 1fr)");
  });

  it("gives the tracks back when the pane is restored", () => {
    const panes = Array.from({ length: 12 }, (_, i) => [`T${i + 1}`, i] as [string, number]);
    renderGrid(sessionWith(panes));
    fireEvent.click(screen.getByTestId("pane-maximize-T3"));
    fireEvent.click(screen.getByTestId("pane-maximize-T3"));
    expect(gridEl().style.gridTemplateColumns).toBe("repeat(6, minmax(0, 1fr))");
  });
});

describe("maximize", () => {
  it("hides the other panes without unmounting them", () => {
    // Unmounting would tear down the WebSocket and kill the agent, so the other
    // panes must still be in the DOM — only hidden.
    renderGrid();
    fireEvent.click(screen.getByTestId("pane-maximize-Mika"));

    const mika = screen.getByTestId("pane-Mika");
    const nova = screen.getByTestId("pane-Nova");
    expect(mika.getAttribute("data-maximized")).toBe("yes");
    expect(nova).toBeTruthy();
    expect(nova.parentElement?.className).toContain("hidden");
    expect(mika.parentElement?.className).not.toContain("hidden");
  });

  it("clicking again restores the grid", () => {
    renderGrid();
    fireEvent.click(screen.getByTestId("pane-maximize-Mika"));
    fireEvent.click(screen.getByTestId("pane-maximize-Mika"));
    expect(screen.getByTestId("pane-Nova").parentElement?.className).not.toContain(
      "hidden",
    );
  });

  it("keeps a stacked pane mounted while another one is maximized", () => {
    renderGrid(
      sessionWith([
        ["Mika", 0, 0],
        ["Nova", 0, 1],
      ]),
    );
    fireEvent.click(screen.getByTestId("pane-maximize-Mika"));
    // Nova is hidden, never removed — removing it would kill its agent.
    expect(screen.getByTestId("pane-Nova")).toBeTruthy();
    expect(screen.getByTestId("pane-cell-Nova").className).toContain("hidden");
  });
});

describe("closing a pane", () => {
  it("asks before killing the agent", () => {
    renderGrid();
    fireEvent.click(screen.getByTestId("pane-close-Mika"));
    expect(screen.getByTestId("confirm-close-terminal")).toBeTruthy();
    expect(screen.getByText(/Close Mika\?/)).toBeTruthy();
    // Nothing has happened yet.
    expect(api.closeTerminal).not.toHaveBeenCalled();
  });

  it("cancelling leaves the terminal alone", () => {
    renderGrid();
    fireEvent.click(screen.getByTestId("pane-close-Mika"));
    fireEvent.click(screen.getByRole("button", { name: /keep it open/i }));
    expect(screen.queryByTestId("confirm-close-terminal")).toBeNull();
    expect(api.closeTerminal).not.toHaveBeenCalled();
  });

  it("confirming closes it and updates the workspace", async () => {
    const { onSessionChanged } = renderGrid();
    fireEvent.click(screen.getByTestId("pane-close-Mika"));
    fireEvent.click(screen.getByTestId("confirm-close-terminal-confirm"));

    await waitFor(() => expect(api.closeTerminal).toHaveBeenCalledWith("Mika"));
    await waitFor(() => expect(onSessionChanged).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByTestId("confirm-close-terminal")).toBeNull(),
    );
  });

  it("escape cancels the dialog", () => {
    renderGrid();
    fireEvent.click(screen.getByTestId("pane-close-Mika"));
    fireEvent.keyDown(screen.getByTestId("confirm-close-terminal"), { key: "Escape" });
    expect(screen.queryByTestId("confirm-close-terminal")).toBeNull();
  });

  it("reports a refused close honestly", async () => {
    vi.mocked(api.closeTerminal).mockRejectedValue(new Error("No terminal called 'Mika'."));
    renderGrid();
    fireEvent.click(screen.getByTestId("pane-close-Mika"));
    fireEvent.click(screen.getByTestId("confirm-close-terminal-confirm"));
    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith("error", "No terminal called 'Mika'."),
    );
  });

  it("offers to open a terminal once the workspace is empty", () => {
    renderGrid(sessionWith([]));
    expect(screen.getByText(/Every terminal in this workspace is closed/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /open a terminal/i })).toBeTruthy();
  });
});

describe("selecting several terminals", () => {
  it("shows a clear selection mode in the toolbar and lets clicks mark panes", () => {
    renderGrid();

    fireEvent.click(screen.getByTestId("terminal-selection-toggle"));
    expect(screen.getByTestId("terminal-selection-actions")).toBeTruthy();
    expect(screen.getByText("Selected: 0")).toBeTruthy();

    const mika = screen.getByTestId("select-terminal-Mika");
    fireEvent.click(mika);
    expect(mika.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("Selected: 1")).toBeTruthy();

    fireEvent.click(mika);
    expect(mika.getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByText("Selected: 0")).toBeTruthy();
  });

  it("never enters selection mode on a right-click", () => {
    renderGrid();

    // The pane keeps the right button for the app-wide Cut/Copy/Paste menu, so
    // the event must also survive untouched rather than being swallowed here.
    const reached = fireEvent.contextMenu(screen.getByTestId("pane-cell-Nova"));

    expect(reached).toBe(true);
    expect(
      screen.getByTestId("terminal-selection-toggle").getAttribute("aria-pressed"),
    ).toBe("false");
    expect(screen.queryByTestId("terminal-selection-actions")).toBeNull();
    expect(screen.queryByTestId("select-terminal-Nova")).toBeNull();
  });

  it("does nothing at all on a right-click while selection mode is on", () => {
    renderGrid();
    fireEvent.click(screen.getByTestId("terminal-selection-toggle"));

    const reached = fireEvent.contextMenu(screen.getByTestId("select-terminal-Nova"));

    expect(reached).toBe(false);
    expect(
      screen.getByTestId("select-terminal-Nova").getAttribute("aria-pressed"),
    ).toBe("false");
    expect(screen.getByText("Selected: 0")).toBeTruthy();
  });

  it("select all marks every terminal with one click", () => {
    renderGrid();
    fireEvent.click(screen.getByTestId("terminal-selection-toggle"));

    fireEvent.click(screen.getByRole("button", { name: "Select all" }));

    expect(screen.getByText("Selected: 2")).toBeTruthy();
    expect(
      screen.getByTestId("select-terminal-Mika").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByTestId("select-terminal-Nova").getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("asks once, then closes every selected terminal in one batch", async () => {
    const { onSessionChanged } = renderGrid();
    fireEvent.click(screen.getByTestId("terminal-selection-toggle"));
    fireEvent.click(screen.getByRole("button", { name: "Select all" }));

    fireEvent.click(screen.getByTestId("close-selected-terminals"));
    const confirmation = screen.getByTestId("confirm-close-selection");
    expect(confirmation.textContent).toContain("Mika");
    expect(confirmation.textContent).toContain("Nova");
    expect(api.closeTerminals).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("confirm-close-selection-confirm"));
    await waitFor(() =>
      expect(api.closeTerminals).toHaveBeenCalledWith(["Mika", "Nova"]),
    );
    expect(api.closeTerminal).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(onSessionChanged).toHaveBeenCalledWith(sessionWith([])),
    );
    expect(screen.queryByTestId("confirm-close-selection")).toBeNull();
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByTestId("terminal-selection-toggle"),
      ),
    );
  });

  it("keeps a failed terminal selected and targeted after a partial close", async () => {
    vi.mocked(api.closeTerminals).mockResolvedValue({
      closed: ["Mika"],
      failed: [{ name: "Nova", detail: "Still stopping." }],
      session: sessionWith([["Nova", 0]]),
    });
    renderGrid();
    fireEvent.click(screen.getByRole("button", { name: /Nova live/i }));
    fireEvent.click(screen.getByTestId("terminal-selection-toggle"));
    fireEvent.click(screen.getByRole("button", { name: "Select all" }));
    fireEvent.click(screen.getByTestId("close-selected-terminals"));
    fireEvent.click(screen.getByTestId("confirm-close-selection-confirm"));

    await waitFor(() =>
      expect(screen.getByPlaceholderText(/instruction for Nova/i)).toBeTruthy(),
    );
    expect(
      screen.getByTestId("select-terminal-Nova").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(pushToast).toHaveBeenCalledWith(
      "error",
      expect.stringContaining("Nova: Still stopping."),
    );
  });

  it("keeps every selected terminal open when confirmation is cancelled", async () => {
    renderGrid();
    fireEvent.click(screen.getByTestId("terminal-selection-toggle"));
    fireEvent.click(screen.getByTestId("select-terminal-Mika"));
    fireEvent.click(screen.getByTestId("close-selected-terminals"));

    fireEvent.click(screen.getByRole("button", { name: "Keep them open" }));

    expect(screen.queryByTestId("confirm-close-selection")).toBeNull();
    expect(api.closeTerminals).not.toHaveBeenCalled();
    expect(
      screen.getByTestId("select-terminal-Mika").getAttribute("aria-pressed"),
    ).toBe("true");
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByTestId("terminal-selection-toggle"),
      ),
    );
  });
});

describe("closing the workspace", () => {
  it("asks before stopping every coding agent and focuses the safe action", async () => {
    const { onClose } = renderGrid();
    fireEvent.click(screen.getByTitle("Close the workspace and stop every agent in it"));

    expect(screen.getByTestId("confirm-close-workspace")).toBeTruthy();
    expect(screen.getByText(/Close this workspace\?/i)).toBeTruthy();
    expect(screen.getByText(/stops all 2 coding agents/i)).toBeTruthy();
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: /keep workspace open/i }),
      ),
    );
    expect(onClose).not.toHaveBeenCalled();
  });

  it("cancelling leaves the workspace and every agent open", () => {
    const { onClose } = renderGrid();
    fireEvent.click(screen.getByTitle("Close the workspace and stop every agent in it"));
    fireEvent.click(screen.getByRole("button", { name: /keep workspace open/i }));

    expect(screen.queryByTestId("confirm-close-workspace")).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("only requests shutdown after explicit confirmation", () => {
    const { onClose } = renderGrid();
    fireEvent.click(screen.getByTitle("Close the workspace and stop every agent in it"));
    fireEvent.click(screen.getByTestId("confirm-close-workspace-confirm"));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("hides workspace controls from assistive technology while confirmation is open", () => {
    renderGrid();
    fireEvent.click(screen.getByTitle("Close the workspace and stop every agent in it"));
    const prompt = screen.getByPlaceholderText(/instruction for Mika/i);

    expect(prompt.closest('[aria-hidden="true"]')).toBeTruthy();
  });

  it("escape safely cancels and restores focus to the close trigger", async () => {
    const { onClose } = renderGrid();
    const trigger = screen.getByTitle("Close the workspace and stop every agent in it");
    fireEvent.click(trigger);
    const cancel = screen.getByRole("button", { name: /keep workspace open/i });
    await waitFor(() => expect(document.activeElement).toBe(cancel));
    fireEvent.keyDown(cancel, { key: "Escape" });

    await waitFor(() => expect(screen.queryByTestId("confirm-close-workspace")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("restarting a dead pane", () => {
  it("bumps only that pane's restart token", () => {
    // An exited agent leaves a pane with nothing in it and no way back. The token
    // is what reconnects it — and it must not disturb the neighbours, whose live
    // agents would die with their sockets.
    renderGrid();
    const before = {
      mika: screen.getByTestId("pane-Mika").getAttribute("data-restart-token"),
      nova: screen.getByTestId("pane-Nova").getAttribute("data-restart-token"),
    };
    fireEvent.click(screen.getByTestId("pane-restart-Mika"));

    expect(screen.getByTestId("pane-Mika").getAttribute("data-restart-token")).not.toBe(
      before.mika,
    );
    expect(screen.getByTestId("pane-Nova").getAttribute("data-restart-token")).toBe(
      before.nova,
    );
  });

  it("can be used more than once", () => {
    renderGrid();
    const pane = () => screen.getByTestId("pane-Mika").getAttribute("data-restart-token");
    const first = pane();
    fireEvent.click(screen.getByTestId("pane-restart-Mika"));
    const second = pane();
    fireEvent.click(screen.getByTestId("pane-restart-Mika"));
    expect(new Set([first, second, pane()]).size).toBe(3);
  });

  it("restarting does not touch the workspace on the server", () => {
    // Reconnecting is a client-side act: the pane's socket closes and reopens, and
    // the backend spawns a fresh agent for it. No pane is added or removed.
    renderGrid();
    fireEvent.click(screen.getByTestId("pane-restart-Mika"));
    expect(api.addTerminal).not.toHaveBeenCalled();
    expect(api.closeTerminal).not.toHaveBeenCalled();
  });
});

describe("the prompt bar composes before it sends", () => {
  const type = (text: string) => {
    const box = screen.getByPlaceholderText(/instruction for Mika/i);
    fireEvent.change(box, { target: { value: text } });
    fireEvent.keyDown(box, { key: "Enter" });
  };

  it("shows the briefed prompt for approval instead of sending straight away", async () => {
    renderGrid();
    type("run the tests");

    await waitFor(() => expect(screen.getByTestId("prompt-preview")).toBeTruthy());
    // The third argument is the dropped-file list — empty here, because this
    // instruction was typed with nothing attached.
    expect(api.composePrompt).toHaveBeenCalledWith("Mika", "run the tests", []);
    expect(api.promptTerminal).not.toHaveBeenCalled();
  });

  it("sends the composed prompt when the user approves it", async () => {
    renderGrid();
    type("run the tests");
    await waitFor(() => expect(screen.getByTestId("prompt-preview")).toBeTruthy());

    fireEvent.click(screen.getByTestId("prompt-preview-send"));

    await waitFor(() =>
      expect(api.promptTerminal).toHaveBeenCalledWith(
        "Mika",
        "## Task\nRun the tests.",
        // Nothing carried alongside: the composed text already contains
        // whatever was dropped, so sending it again would duplicate it.
        { attachments: [] },
      ),
    );
  });

  it("sends the user's own wording when they prefer it", async () => {
    renderGrid();
    type("run the tests");
    await waitFor(() => expect(screen.getByTestId("prompt-preview")).toBeTruthy());

    fireEvent.click(screen.getByTestId("prompt-preview-verbatim"));

    await waitFor(() =>
      expect(api.promptTerminal).toHaveBeenCalledWith("Mika", "run the tests", {
        attachments: [],
      }),
    );
  });

  it("gives the typed text back when the preview is discarded", async () => {
    renderGrid();
    type("run the tests");
    await waitFor(() => expect(screen.getByTestId("prompt-preview")).toBeTruthy());

    fireEvent.click(screen.getByTestId("prompt-preview-cancel"));

    await waitFor(() => expect(screen.queryByTestId("prompt-preview")).toBeNull());
    expect(
      (screen.getByPlaceholderText(/instruction for Mika/i) as HTMLTextAreaElement).value,
    ).toBe("run the tests");
    expect(api.promptTerminal).not.toHaveBeenCalled();
  });

  it("still delivers the instruction when composing fails outright", async () => {
    vi.mocked(api.composePrompt).mockRejectedValue(new Error("no session"));
    renderGrid();
    type("run the tests");

    await waitFor(() =>
      expect(api.promptTerminal).toHaveBeenCalledWith("Mika", "run the tests", {
        attachments: [],
      }),
    );
  });
});

/*
 * Dropping a screenshot on the prompt bar.
 *
 * This is the gesture the whole feature exists for, and its failure mode is
 * quiet: a user drops a picture of a broken layout, types "fix this", and the
 * agent — which frequently cannot open an image at all — receives a path and a
 * pronoun. So what is pinned here is that the CONTENTS of the file reach the
 * composition, not merely that a drop was accepted.
 */
describe("dropping files on the prompt bar", () => {
  /** A DataTransfer stand-in; jsdom cannot construct a real one. */
  function transfer(types: string[]) {
    return {
      types,
      files: [],
      items: [],
      dropEffect: "none",
      getData: (kind: string) =>
        kind === "text/uri-list" ? "file:///C:/work/shot.png" : "",
    } as unknown as DataTransfer;
  }

  const drop = (types: string[] = ["Files"]) =>
    fireEvent.drop(screen.getByTestId("agentic-composer"), {
      dataTransfer: transfer(types),
    });

  it("reads the dropped file instead of typing its path into the pane", async () => {
    renderGrid();

    drop();

    await waitFor(() => expect(api.attachToTerminal).toHaveBeenCalled());
    const [name, payload] = vi.mocked(api.attachToTerminal).mock.calls[0];
    expect(name).toBe("Mika");
    expect(payload.analyze).toBe(true);
    // Held rather than typed: the user is still writing the sentence that
    // explains the file, and it goes in with that sentence.
    expect(payload.deliver).toBe(false);
    expect(payload.paths).toEqual(["C:/work/shot.png"]);
  });

  it("shows what was read out of the file, not just its name", async () => {
    renderGrid();

    drop();

    await waitFor(() => expect(screen.getByTestId("agentic-attachments")).toBeTruthy());
    const strip = screen.getByTestId("agentic-attachments");
    expect(strip.textContent).toContain("shot.png");
    expect(strip.textContent).toContain("described");
  });

  it("carries the analysis into the composition", async () => {
    renderGrid();
    drop();
    await waitFor(() => expect(screen.getByTestId("agentic-attachments")).toBeTruthy());

    const box = screen.getByPlaceholderText(/instruction for Mika/i);
    fireEvent.change(box, { target: { value: "fix this" } });
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() => expect(api.composePrompt).toHaveBeenCalled());
    const attachments = vi.mocked(api.composePrompt).mock.calls[0][2];
    expect(attachments).toHaveLength(1);
    expect(attachments?.[0].detail).toContain("submit button overflows");
  });

  it("keeps the attachment when the user backs out of the rewrite", async () => {
    // Discarding a proposed wording is not a reason to lose the dropped file.
    renderGrid();
    drop();
    await waitFor(() => expect(screen.getByTestId("agentic-attachments")).toBeTruthy());

    const box = screen.getByPlaceholderText(/instruction for Mika/i);
    fireEvent.change(box, { target: { value: "fix this" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.getByTestId("prompt-preview")).toBeTruthy());

    fireEvent.click(screen.getByTestId("prompt-preview-cancel"));

    expect(screen.getByTestId("agentic-attachments").textContent).toContain("shot.png");
  });

  it("lets an attachment be taken back off", async () => {
    renderGrid();
    drop();
    await waitFor(() => expect(screen.getByTestId("agentic-attachments")).toBeTruthy());

    fireEvent.click(screen.getByTestId("agentic-attachment-remove-shot.png"));

    expect(screen.queryByTestId("agentic-attachment-shot.png")).toBeNull();
  });

  it("clears the attachments once they have been sent", async () => {
    renderGrid();
    drop();
    await waitFor(() => expect(screen.getByTestId("agentic-attachments")).toBeTruthy());

    const box = screen.getByPlaceholderText(/instruction for Mika/i);
    fireEvent.change(box, { target: { value: "fix this" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.getByTestId("prompt-preview")).toBeTruthy());
    fireEvent.click(screen.getByTestId("prompt-preview-send"));

    // Otherwise the next, unrelated instruction would silently carry the old
    // screenshot along with it.
    await waitFor(() => expect(screen.queryByTestId("agentic-attachments")).toBeNull());
  });

  it("ignores a drag carrying only selected text", async () => {
    renderGrid();

    drop(["text/plain"]);

    expect(api.attachToTerminal).not.toHaveBeenCalled();
  });

  it("reports an analysis that came back empty rather than pretending", async () => {
    vi.mocked(api.attachToTerminal).mockResolvedValue({
      terminal: "Mika",
      references: [],
      files: [],
      copied: 0,
      submitted: false,
      delivered: false,
      analysis: [],
    });
    renderGrid();

    drop();

    await waitFor(() => expect(pushToast).toHaveBeenCalled());
    expect(pushToast.mock.calls[0][0]).toBe("warning");
  });

  it("surfaces a failed attach instead of losing the drop silently", async () => {
    vi.mocked(api.attachToTerminal).mockRejectedValue(new Error("pane is gone"));
    renderGrid();

    drop();

    await waitFor(() => expect(pushToast).toHaveBeenCalledWith("error", "pane is gone"));
  });
});

/*
 * The seam between the terminals and the prompt bar.
 *
 * jsdom reports every element as 0×0, so the measured ceiling falls back to the
 * designed height — which is why these tests exercise the directions that do
 * not need a taller window: collapsing the bar, reopening it, and remembering
 * the choice. Growing it is verified live in the browser.
 */
describe("prompt bar seam", () => {
  const HEIGHT_KEY = "jarvis.agenticIde.composerHeight.v1";

  afterEach(() => window.localStorage.clear());

  /** Drag the seam from `fromY` to `toY`, start to finish. */
  function dragSeam(fromY: number, toY: number) {
    const seam = screen.getByTestId("pane-resizer-horizontal");
    fireEvent.pointerDown(seam, { clientY: fromY });
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { clientY: toY }));
    });
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup"));
    });
  }

  it("puts a draggable seam above the prompt bar", () => {
    renderGrid();
    const seam = screen.getByTestId("pane-resizer-horizontal");
    expect(seam.getAttribute("role")).toBe("separator");
    expect(seam.getAttribute("aria-orientation")).toBe("horizontal");
  });

  it("dragging the seam to the bottom collapses the bar to a strip", () => {
    renderGrid();
    expect(screen.getByTestId("agentic-composer")).toBeTruthy();

    dragSeam(700, 1400);

    expect(screen.queryByTestId("agentic-composer")).toBeNull();
    expect(screen.getByTestId("agentic-composer-collapsed")).toBeTruthy();
    // The collapsed strip is a strip, not a disappearance: the way back has to
    // stay on screen, or the workspace loses its input box for good.
    expect(screen.getByTestId("agentic-composer-reopen")).toBeTruthy();
    expect(screen.getByTestId("pane-resizer-horizontal")).toBeTruthy();
  });

  it("the reopen button brings the prompt bar back at its designed height", () => {
    window.localStorage.setItem(HEIGHT_KEY, "34");
    renderGrid();
    expect(screen.getByTestId("agentic-composer-collapsed")).toBeTruthy();

    fireEvent.click(screen.getByTestId("agentic-composer-reopen"));

    const composer = screen.getByTestId("agentic-composer");
    expect(composer.style.height).toBe("176px");
    expect(screen.getByPlaceholderText(/instruction for Mika/i)).toBeTruthy();
  });

  it("remembers a collapsed bar across a remount", () => {
    renderGrid();
    dragSeam(700, 1400);
    expect(window.localStorage.getItem(HEIGHT_KEY)).toBe("34");

    cleanup();
    renderGrid();
    expect(screen.getByTestId("agentic-composer-collapsed")).toBeTruthy();
  });

  it("double-clicking the seam restores the designed height", () => {
    window.localStorage.setItem(HEIGHT_KEY, "34");
    renderGrid();

    fireEvent.doubleClick(screen.getByTestId("pane-resizer-horizontal"));

    expect(screen.getByTestId("agentic-composer").style.height).toBe("176px");
  });
});


describe("a workspace with far more panes than the window fits", () => {
  /** Many panes, one per column, the way a big fan-out opens them. */
  function manyPanes(count: number) {
    return sessionWith(
      Array.from({ length: count }, (_, i) => [`T${i}`, i] as [string, number]),
    );
  }

  function grid(): HTMLElement {
    const cell = screen.getAllByTestId(/^pane-cell-/)[0];
    const container = cell.parentElement;
    if (!container) throw new Error("no grid container");
    return container;
  }

  it("keeps every pane readable instead of sharing the height N ways", () => {
    // The failure this guards: rows used to be a free `1fr`, so panes shrank
    // without limit. Measured on a 2560 px screen, 12 panes gave each ~26 text
    // rows, 40 gave 7 and 100 gave 3 — readable width, unusable height, and
    // nothing crashed to tell anyone.
    renderGrid(manyPanes(40));
    const rows = grid().style.gridTemplateRows;
    expect(rows).toContain("240px");
    expect(rows).not.toContain("minmax(0,");
  });

  it("scrolls once the panes stop fitting, rather than squeezing them", () => {
    renderGrid(manyPanes(40));
    expect(grid().className).toContain("overflow-y-auto");
  });

  it("renders a pane for every one of a hundred terminals", () => {
    // The backend cap; nothing may be silently dropped on the way to the screen.
    renderGrid(manyPanes(100));
    expect(screen.getAllByTestId(/^pane-cell-/)).toHaveLength(100);
  });
});

/*
 * The pane headers say what each session is doing, and that sentence goes stale
 * in seconds. So the grid polls for it — separately from the workspace state,
 * which changes only when a pane is opened, closed or moved.
 */
describe("session recaps", () => {
  it("asks the backend what its own workspace's panes are doing", async () => {
    renderGrid();

    await waitFor(() =>
      expect(api.fetchTerminalRecaps).toHaveBeenCalledWith("ide_test"),
    );
  });

  it("hands each pane the recap that came back for it", async () => {
    vi.mocked(api.fetchTerminalRecaps).mockResolvedValue({
      workspace_id: "ide_test",
      terminals: [
        {
          key: "mika",
          name: "Mika",
          status: "live",
          recap: "Running pytest tests/unit/test_login.py",
          recap_detail:
            'Last asked to: "Fix the failing login test". Working now: Running pytest.',
        },
      ],
    });

    renderGrid();

    await waitFor(() =>
      expect(screen.getByTestId("pane-Mika").dataset.recap).toBe(
        "Running pytest tests/unit/test_login.py",
      ),
    );
    expect(screen.getByTestId("pane-Mika").dataset.recapDetail).toContain(
      "Fix the failing login test",
    );
  });

  it("falls back to the recap the workspace state carried", async () => {
    // Nothing polled yet (and nothing ever will, here) — a pane must still open
    // with a sentence in its header rather than with a blank that fills in.
    vi.mocked(api.fetchTerminalRecaps).mockRejectedValue(new Error("offline"));
    const session = sessionWith([["Mika", 0]]);
    session.terminals[0].recap = "Waiting for its first instruction.";

    renderGrid(session);

    await waitFor(() =>
      expect(screen.getByTestId("pane-Mika").dataset.recap).toBe(
        "Waiting for its first instruction.",
      ),
    );
  });
});
