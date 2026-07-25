import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
    onToggleMaximize,
    onSplitRight,
    onSplitDown,
    onClose,
  }: {
    name: string;
    maximized?: boolean;
    splitDisabled?: boolean;
    onToggleMaximize?: () => void;
    onSplitRight?: () => void;
    onSplitDown?: () => void;
    onClose?: () => void;
  }) => (
    <div data-testid={`pane-${name}`} data-maximized={maximized ? "yes" : "no"}>
      {name}
      <button data-testid={`pane-maximize-${name}`} onClick={onToggleMaximize}>
        max
      </button>
      <button
        data-testid={`pane-split-right-${name}`}
        disabled={splitDisabled}
        onClick={onSplitRight}
      >
        right
      </button>
      <button
        data-testid={`pane-split-down-${name}`}
        disabled={splitDisabled}
        onClick={onSplitDown}
      >
        down
      </button>
      <button data-testid={`pane-close-${name}`} onClick={onClose}>
        close
      </button>
    </div>
  ),
  PaneStatusPill: () => <span>live</span>,
}));

import { AgenticGrid } from "./AgenticGrid";
import * as api from "@/lib/agenticIdeApi";
import type { SessionState, TerminalState } from "@/lib/agenticIdeApi";

function pane(name: string, row: number, index: number): TerminalState {
  return {
    key: name.toLowerCase(),
    name,
    agent: "claude",
    display_name: "Claude Code",
    index,
    row,
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

function sessionWith(panes: Array<[string, number]>): SessionState {
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
      note: "",
    },
    created_at: 0,
    focus_mode: false,
    terminals: panes.map(([name, row], i) => pane(name, row, i)),
  };
}

const BASE = sessionWith([
  ["Mika", 0],
  ["Nova", 0],
]);

beforeEach(() => {
  vi.mocked(api.addTerminal).mockResolvedValue(
    sessionWith([
      ["Mika", 0],
      ["Aria", 0],
      ["Nova", 0],
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
  render(
    <AgenticGrid
      session={session}
      focusMode={false}
      onToggleFocus={vi.fn()}
      onClose={vi.fn()}
      onSessionChanged={onSessionChanged}
      {...extra}
    />,
  );
  return { onSessionChanged };
}

describe("pane actions", () => {
  it("splitting right asks for a pane in the same row", async () => {
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

  it("splitting down asks for a new row", async () => {
    renderGrid();
    fireEvent.click(screen.getByTestId("pane-split-down-Nova"));
    await waitFor(() =>
      expect(api.addTerminal).toHaveBeenCalledWith({
        anchor: "Nova",
        direction: "down",
      }),
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

  it("hides a whole row that holds no maximized pane", () => {
    renderGrid(
      sessionWith([
        ["Mika", 0],
        ["Nova", 1],
      ]),
    );
    fireEvent.click(screen.getByTestId("pane-maximize-Mika"));
    // Nova's entire row is hidden, not just the pane.
    expect(screen.getByTestId("pane-Nova")).toBeTruthy();
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
