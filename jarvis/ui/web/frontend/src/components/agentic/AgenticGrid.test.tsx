import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  closeTerminal: vi.fn(),
  promptTerminal: vi.fn(),
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
    expect(gridEl().style.gridTemplateRows).toBe("repeat(1, minmax(0, 1fr))");
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
    expect(gridEl().style.gridTemplateRows).toBe("repeat(2, minmax(0, 1fr))");
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
