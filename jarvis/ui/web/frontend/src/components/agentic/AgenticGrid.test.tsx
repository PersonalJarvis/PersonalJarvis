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

/**
 * How often each pane has been rendered.
 *
 * A pane in the real grid is a live terminal with a header, a recap card and a
 * scrollbar, so "how often is it re-rendered" is not a detail — it is what
 * decides whether dragging a boundary runs at sixty frames a second or at
 * three. Counted here so the drag tests can pin it.
 *
 * Through `vi.hoisted` because `vi.mock` factories are lifted above ordinary
 * module code and would otherwise reach a `const` that does not exist yet.
 */
const { paneRenders, paneActiveHistory } = vi.hoisted(() => ({
  paneRenders: new Map<string, number>(),
  paneActiveHistory: new Map<string, boolean[]>(),
}));

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
  moveTerminal: vi.fn(),
  renameTerminal: vi.fn(),
  saveLayoutWeights: vi.fn(),
  // Polled by the grid so the pane headers keep saying what their agents are
  // doing. Resolves empty by default; the recap tests give it real rows.
  fetchTerminalRecaps: vi.fn(async () => ({
    workspace_id: "ide_test",
    terminals: [],
  })),
  // The status badges' fast poll — one stamped word per pane, no recap.
  fetchTerminalActivity: vi.fn(async () => ({
    workspace_id: "ide_test",
    terminals: [],
  })),
  fetchWorkspaceFiles: vi.fn(async () => ({
    workspace_id: "ide_test",
    root_name: "project",
    path: "",
    entries: [],
    truncated: false,
  })),
  fetchWorkspaceFilePreview: vi.fn(),
  workspaceFileUrl: (workspaceId: string, path: string) =>
    `/api/agentic-ide/workspaces/${workspaceId}/file?path=${encodeURIComponent(path)}`,
  openTerminalTarget: vi.fn(),
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
  // Polled by the header bell, which the grid also always renders. Answers
  // "nothing has stopped" so these tests see a plain toolbar rather than a
  // badge none of them are about.
  fetchPaneNotifications: vi.fn(async () => ({
    enabled: true,
    unread: 0,
    notifications: [],
  })),
  markPaneNotificationsRead: vi.fn(async () => 0),
  clearPaneNotifications: vi.fn(async () => undefined),
  // The remembered terminal text size, read once per mounted workspace.
  // Answers "nothing chosen yet" by default so these tests see the default
  // size; the text-size tests give it a stored one.
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
  syncAgenticIdeSurface: vi.fn(async () => undefined),
}));

// The grid follows the app theme for its terminal colours; these tests render
// it outside the provider, so the hook is stubbed rather than the whole app.
vi.mock("@/hooks/useTheme", () => ({
  useThemeValue: () => "dark",
}));

/**
 * The pane header's press handler, as much of it as the stub needs. Spelled out
 * rather than borrowed from React's types so the stub stays a stub.
 */
type PointerEventLike = { clientX: number; clientY: number; button: number };

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
    onRename,
    onClose,
    onArrangeStart,
    arranging,
    layoutBusy,
    fontSize,
    active,
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
    onRename?: (name: string) => Promise<boolean>;
    onClose?: () => void;
    onArrangeStart?: (event: PointerEventLike) => void;
    arranging?: boolean;
    layoutBusy?: boolean;
    fontSize?: number;
    active?: boolean;
  }) => {
    paneRenders.set(name, (paneRenders.get(name) ?? 0) + 1);
    paneActiveHistory.set(name, [
      ...(paneActiveHistory.get(name) ?? []),
      active ?? true,
    ]);
    return (
    <div
      data-testid={`pane-${name}`}
      data-maximized={maximized ? "yes" : "no"}
      data-agents={(agents ?? []).map((a) => a.name).join(",")}
      data-restart-token={String(restartToken ?? 0)}
      data-recap={recap ?? ""}
      data-recap-detail={recapDetail ?? ""}
      // Whether this pane offers the drag at all, and whether it is the one
      // currently in hand — the real header draws both; here they are read.
      data-arrangeable={onArrangeStart ? "yes" : "no"}
      data-arranging={arranging ? "yes" : "no"}
      // The real pane stops refitting its terminal while this is on. Read here
      // because it is the grid's job to say WHEN the geometry is in motion.
      data-layout-busy={layoutBusy ? "yes" : "no"}
      data-active={active ? "yes" : "no"}
      // The size the terminal text is drawn at. Read here because the point of
      // remembering it is that the PANES come back at that size.
      data-font-size={String(fontSize ?? "")}
    >
      {name}
      {/* Stands for the pane header, which is the grip in the real component. */}
      <div data-testid={`pane-drag-${name}`} onPointerDown={onArrangeStart} />
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
      {/* The real editor lives in the pane header; here it stands for "the user
          typed a new call-sign and pressed Save". */}
      <button
        data-testid={`pane-rename-${name}`}
        onClick={() => void onRename?.("Frontend")}
      >
        rename
      </button>
      <button data-testid={`pane-close-${name}`} onClick={onClose}>
        close
      </button>
      <button data-testid={`pane-restart-${name}`} onClick={onRestart}>
        restart
      </button>
    </div>
    );
  },
}));

import { AgenticGrid } from "./AgenticGrid";
import * as api from "@/lib/agenticIdeApi";
import type { SessionState, TerminalState } from "@/lib/agenticIdeApi";
import type { LayoutNode } from "./treeLayout";

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

/**
 * The split tree the backend would send for panes at these (column, slot)
 * places — a row of stacks, every share even. Tests that are about UNEVEN
 * shares pass an explicit `layout` instead.
 */
function treeFromGrid(terminals: TerminalState[]): LayoutNode | null {
  const byColumn = new Map<number, TerminalState[]>();
  for (const term of terminals) {
    byColumn.set(term.column, [...(byColumn.get(term.column) ?? []), term]);
  }
  const columns: LayoutNode[] = [...byColumn.keys()]
    .sort((a, b) => a - b)
    .map((column) => {
      const stack = (byColumn.get(column) ?? []).sort((a, b) => a.slot - b.slot);
      return stack.length === 1
        ? { pane: stack[0].key }
        : {
            direction: "column" as const,
            children: stack.map((term) => ({ pane: term.key })),
            weights: stack.map(() => 1),
          };
    });
  if (columns.length === 0) return null;
  if (columns.length === 1) return columns[0];
  return { direction: "row", children: columns, weights: columns.map(() => 1) };
}

/** `panes` are [name, column, slot] triples; slot defaults to 0 (one row). */
function sessionWith(
  panes: Array<[string, number] | [string, number, number]>,
  layout?: LayoutNode | null,
): SessionState {
  const terminals = panes.map(([name, column, slot], i) => pane(name, column, slot ?? 0, i));
  return {
    id: "ide_test",
    layout: layout === undefined ? treeFromGrid(terminals) : layout,
    terminals,
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
  };
}

const BASE = sessionWith([
  ["Mika", 0],
  ["Nova", 1],
]);

/*
 * Open the prompt bar for the suites that are ABOUT the prompt bar.
 *
 * A fresh workspace now opens with it collapsed (the panes are what the user
 * came for — see the seam suite at the bottom), so every test that types an
 * instruction, picks a target or drops a file has to open it first. Doing that
 * through the remembered height rather than a click keeps those tests about
 * what they are testing; the seam suite clears the key again and drives the
 * collapse/reopen behaviour itself.
 */
beforeEach(() => {
  // Pane sizes are remembered per workspace, and every test here uses the same
  // workspace id — so without this a test that splits or drags a seam would
  // hand its sizes to the next one and the layout assertions would depend on
  // the order the tests happen to run in.
  window.localStorage.clear();
  paneActiveHistory.clear();
  window.localStorage.setItem("jarvis.agenticIde.composerHeight.v2", "176");
  // Seam drags post their result fire-and-forget; an auto-mock returning
  // undefined would crash the `.catch` chain rather than fail an assertion.
  vi.mocked(api.saveLayoutWeights).mockResolvedValue(sessionWith([]));
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
  vi.mocked(api.promptTerminal).mockResolvedValue({
    terminal: "Mika",
    sent: "## Task\nRun the tests.",
    composed_by: "llm",
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
  const props = (override: Record<string, unknown>) => (
    <AgenticGrid
      session={session}
      focusMode={false}
      onToggleFocus={vi.fn()}
      onClose={onClose}
      onSessionChanged={onSessionChanged}
      {...extra}
      {...override}
    />
  );
  const view = render(props({}));
  // Re-render the SAME grid with a prop changed — for the tests that assert on
  // what a live grid does when something about it changes, as opposed to how a
  // fresh one starts up.
  const rerender = (override: Record<string, unknown>) =>
    view.rerender(props(override));
  return { onSessionChanged, onClose, rerender };
}

describe("prompt target bridge", () => {
  it("keeps the voice orb aimed at the written prompt target", async () => {
    const onPromptTargetChange = vi.fn();
    renderGrid(BASE, { onPromptTargetChange });

    await waitFor(() => expect(onPromptTargetChange).toHaveBeenCalledWith("Mika"));
    fireEvent.click(screen.getByTestId("prompt-target-Nova"));
    await waitFor(() => expect(onPromptTargetChange).toHaveBeenLastCalledWith("Nova"));
  });
});

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
      expect(screen.getByLabelText(/instruction for Aria/i)).toBeTruthy(),
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
    expect(screen.getByLabelText(/instruction for Mika/i)).toBeTruthy();
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
    expect(screen.getByLabelText(/instruction for Mika/i)).toBeTruthy();
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

/** The inline style of a pane's cell, as written (jsdom keeps it verbatim). */
function cellStyle(name: string): string {
  return screen.getByTestId(`pane-cell-${name}`).getAttribute("style") ?? "";
}

/**
 * A pane's rectangle, as the four percentages it was positioned with.
 *
 * Panes are placed by FRACTION now rather than by grid track, because one CSS
 * grid shares `grid-template-columns` across all of its rows — so two bands
 * could never have had different column widths, and every pane in a band was
 * stuck the same size as its neighbours.
 *
 * Percentages of the WORKSPACE, which is the window — so these numbers are also
 * the proof that a workspace never grows past what is on screen.
 */
function box(name: string): { left: number; top: number; width: number; height: number } {
  const style = cellStyle(name);
  const read = (property: string) => {
    const match = new RegExp(`${property}: (?:calc\\()?([-\\d.]+)%`).exec(style);
    if (!match) throw new Error(`${name} has no ${property}: ${style}`);
    return Number(match[1]);
  };
  return {
    left: read("left"),
    top: read("top"),
    width: read("width"),
    height: read("height"),
  };
}

describe("grid layout", () => {
  it("puts a fresh workspace side by side in one row", () => {
    renderGrid(sessionWith([["Mika", 0], ["Nova", 1], ["Aria", 2], ["Kai", 3]]));
    expect(box("Mika")).toMatchObject({ left: 0, top: 0, width: 25, height: 100 });
    expect(box("Nova").left).toBe(25);
    expect(box("Kai").left).toBe(75);
  });

  it("keeps four splits side by side — the row is the user's choice", async () => {
    // Reported 2026-07-31: an aspect-ratio rule re-wrapped the workspace to
    // 2 x 2 on the fourth "split right", moving a pane the user was reading to
    // another row. The rule is gone — width alone wraps a workspace, so four
    // panes in a 2K window stay in the one row the user built.
    const previous = globalThis.ResizeObserver;
    class AreaObserver {
      constructor(private readonly callback: ResizeObserverCallback) {}
      observe(): void {
        this.callback(
          [{ contentRect: { width: 2048, height: 1100 } } as ResizeObserverEntry],
          this as unknown as ResizeObserver,
        );
      }
      unobserve(): void {}
      disconnect(): void {}
    }
    globalThis.ResizeObserver = AreaObserver as unknown as typeof ResizeObserver;
    try {
      renderGrid(sessionWith([["Mika", 0], ["Nova", 1], ["Aria", 2], ["Kai", 3]]));
      await waitFor(() =>
        expect(box("Aria")).toMatchObject({ left: 50, top: 0, width: 25, height: 100 }),
      );
      expect(box("Kai")).toMatchObject({ left: 75, top: 0, width: 25, height: 100 });
    } finally {
      globalThis.ResizeObserver = previous;
    }
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
    expect(box("Mika").height).toBe(100);
    expect(box("Aria").height).toBe(100);
    expect(box("Nova")).toMatchObject({ top: 0, height: 50 });
    expect(box("Vega")).toMatchObject({ top: 50, height: 50 });
    // Both panes of the split share one column, so they share its left edge.
    expect(box("Vega").left).toBe(box("Nova").left);
    expect(box("Vega").width).toBe(box("Nova").width);
  });

  it("keeps a crowded workspace on ONE line, never re-dealing it", () => {
    // Reported 2026-08-03: past a width-derived ceiling the extra columns used
    // to start a second line — which paid for them with the height of every
    // pane already open, and moved a pane the user was reading. Twelve columns
    // are twelve columns now, sharing the one window between them.
    const panes = Array.from({ length: 12 }, (_, i) => [`T${i + 1}`, i] as [string, number]);
    renderGrid(sessionWith(panes));
    expect(box("T1")).toMatchObject({ left: 0, top: 0, height: 100 });
    expect(box("T11")).toMatchObject({ top: 0, height: 100 });
    expect(box("T11").left).toBeGreaterThan(box("T10").left);
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
    // Without this the pane would stay in its one-twelfth rectangle while the
    // rest of the workspace is blank.
    expect(cellStyle("T3")).toContain("inset: 0");
    // ...and the surface it fills is exactly the window — which is now true of
    // every pane, maximized or not. A maximized pane taller than the visible
    // area made the terminal fit itself to rows below the clip, which is where
    // the CLI keeps its prompt box.
    expect(screen.getByTestId("agentic-grid").className).toContain("overflow-hidden");
    expect(screen.getByTestId("agentic-grid-canvas").style.height).toBe("");
    // Nothing to divide while one pane covers the others.
    expect(screen.queryAllByTestId(/^pane-seam-/)).toHaveLength(0);
  });

  it("gives the panes their own rectangles back when one is restored", () => {
    const panes = Array.from({ length: 12 }, (_, i) => [`T${i + 1}`, i] as [string, number]);
    renderGrid(sessionWith(panes));
    const before = box("T11");
    fireEvent.click(screen.getByTestId("pane-maximize-T3"));
    fireEvent.click(screen.getByTestId("pane-maximize-T3"));
    expect(box("T11")).toMatchObject(before);
  });
});

describe("jump to pane", () => {
  /*
   * What a notification is FOR. An entry says "T3 finished" and the only useful
   * next move is to read T3 — which in a grid of twelve means making it big
   * enough to read. A jump that merely scrolled would land the user back in
   * front of the postcard they could not read in the first place.
   */
  it("maximizes the pane it was asked for", () => {
    const panes = Array.from({ length: 12 }, (_, i) => [`T${i + 1}`, i] as [string, number]);
    const { rerender } = renderGrid(sessionWith(panes));

    rerender({ jumpTo: { pane: "T7", nonce: 1 } });

    expect(screen.getByTestId("pane-T7").getAttribute("data-maximized")).toBe("yes");
    // ...and the eleven it covers are hidden rather than unmounted — the same
    // rule as an ordinary maximize, because unmounting kills the agent.
    expect(screen.getByTestId("pane-T2").parentElement?.className).toContain("hidden");
  });

  it("can be sent to the same pane twice", () => {
    // The nonce is the whole reason the prop is an object: an effect cannot
    // re-fire on a value that has not changed, so a second jump to the pane the
    // user has since restored would silently do nothing.
    const { rerender } = renderGrid();
    rerender({ jumpTo: { pane: "Nova", nonce: 1 } });
    fireEvent.click(screen.getByTestId("pane-maximize-Nova"));
    expect(screen.getByTestId("pane-Nova").getAttribute("data-maximized")).toBe("no");

    rerender({ jumpTo: { pane: "Nova", nonce: 2 } });

    expect(screen.getByTestId("pane-Nova").getAttribute("data-maximized")).toBe("yes");
  });

  it("says so rather than maximizing a stranger when the pane is gone", () => {
    // An entry outlives the terminal it came from by up to one poll, and pane
    // names are reused: "whatever is called T4 now" is the wrong terminal.
    const { rerender } = renderGrid();

    rerender({ jumpTo: { pane: "Ghost", nonce: 1 } });

    expect(screen.getByTestId("pane-Mika").getAttribute("data-maximized")).toBe("no");
    expect(pushToast).toHaveBeenCalledWith("warning", expect.stringContaining("Ghost"));
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
    fireEvent.click(screen.getByTestId("prompt-target-Nova"));
    fireEvent.click(screen.getByTestId("terminal-selection-toggle"));
    fireEvent.click(screen.getByRole("button", { name: "Select all" }));
    fireEvent.click(screen.getByTestId("close-selected-terminals"));
    fireEvent.click(screen.getByTestId("confirm-close-selection-confirm"));

    await waitFor(() =>
      expect(screen.getByLabelText(/instruction for Nova/i)).toBeTruthy(),
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
    const prompt = screen.getByLabelText(/instruction for Mika/i);

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

describe("the prompt bar composes as it sends", () => {
  const type = (text: string) => {
    const box = screen.getByLabelText(/instruction for Mika/i);
    fireEvent.change(box, { target: { value: text } });
    fireEvent.keyDown(box, { key: "Enter" });
  };

  it("asks the backend to compose in the same request that sends", async () => {
    // One step, exactly like the spoken "prompt Mika …": the backend writes
    // the brief and types it in. The retired approval preview held the brief
    // for a click here, and its fallback paths sent the raw text.
    renderGrid();
    type("run the tests");

    await waitFor(() =>
      expect(api.promptTerminal).toHaveBeenCalledWith("Mika", "run the tests", {
        compose: true,
        attachments: [],
      }),
    );
  });

  it("clears the editor once the instruction was delivered", async () => {
    renderGrid();
    type("run the tests");

    await waitFor(() =>
      expect(
        (screen.getByLabelText(/instruction for Mika/i) as HTMLTextAreaElement).value,
      ).toBe(""),
    );
  });

  it("keeps the draft when the request fails, instead of sending it raw", async () => {
    // A failed request typed nothing anywhere. Retrying it verbatim would
    // reintroduce the exact behaviour this path exists to rule out, so the
    // words stay in the editor and the failure is said out loud.
    vi.mocked(api.promptTerminal).mockRejectedValue(new Error("no session"));
    renderGrid();
    type("run the tests");

    await waitFor(() => expect(pushToast).toHaveBeenCalledWith("error", "no session"));
    expect(api.promptTerminal).toHaveBeenCalledTimes(1);
    expect(
      (screen.getByLabelText(/instruction for Mika/i) as HTMLTextAreaElement).value,
    ).toBe("run the tests");
  });

  it("warns when the pane held the prompt without starting on it", async () => {
    vi.mocked(api.promptTerminal).mockResolvedValue({
      terminal: "Mika",
      sent: "## Task\nRun the tests.",
      composed_by: "llm",
      files: [],
      submitted: false,
      detail: "Mika did not accept the prompt.",
    });
    renderGrid();
    type("run the tests");

    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith("warning", "Mika did not accept the prompt."),
    );
  });
});

describe("the compose narration line", () => {
  // Composition is 10-30 s of model work; the backend narrates each beat over
  // the app socket, and the bar shows the latest line so a working composer
  // and a wedged one stop looking identical.
  const beat = (detail: Record<string, unknown>) =>
    fireEvent(
      window,
      new CustomEvent("jarvis:agentic-ide-compose", { detail }),
    );

  it("shows the latest beat while the brief is being written", () => {
    renderGrid();

    beat({
      session_id: "ide_test",
      terminal: "Mika",
      stage: "thinking",
      message: "Reading the code before Mika is briefed - 3 file outlines.",
    });

    expect(screen.getByTestId("agentic-compose-progress").textContent).toContain(
      "Reading the code before Mika is briefed",
    );
  });

  it("clears the line once the delivery is announced", () => {
    renderGrid();
    beat({ session_id: "ide_test", terminal: "Mika", stage: "drafting", message: "Writing." });

    fireEvent(
      window,
      new CustomEvent("jarvis:agentic-ide-prompt", {
        detail: { terminal: "Mika", submitted: true },
      }),
    );

    expect(screen.queryByTestId("agentic-compose-progress")).toBeNull();
  });

  it("ignores beats that belong to another workspace", () => {
    renderGrid();

    beat({ session_id: "ide_other", terminal: "Mika", stage: "start", message: "Writing." });

    expect(screen.queryByTestId("agentic-compose-progress")).toBeNull();
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

    const box = screen.getByLabelText(/instruction for Mika/i);
    fireEvent.change(box, { target: { value: "fix this" } });
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() => expect(api.promptTerminal).toHaveBeenCalled());
    const options = vi.mocked(api.promptTerminal).mock.calls[0][2];
    expect(options?.compose).toBe(true);
    expect(options?.attachments).toHaveLength(1);
    expect(options?.attachments?.[0].detail).toContain("submit button overflows");
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

    const box = screen.getByLabelText(/instruction for Mika/i);
    fireEvent.change(box, { target: { value: "fix this" } });
    fireEvent.keyDown(box, { key: "Enter" });

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
  const HEIGHT_KEY = "jarvis.agenticIde.composerHeight.v2";
  /** The height the strip collapses to, and the one it opens back up to. */
  const SHUT = "28";
  const OPEN = "176";

  // This suite owns the remembered height — drop what the file-wide setup put
  // there so each test below starts from the state it actually describes.
  beforeEach(() => window.localStorage.removeItem(HEIGHT_KEY));
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

  /*
   * A workspace this view has never been sized in opens with the bar SHUT.
   *
   * The panes are what the user came for; a 176 px writing surface held open
   * under a dozen of them took a sixth of each pane's height for an input box
   * that is empty most of the time — and everything typed there can be said out
   * loud instead. The strip below it is what keeps that recoverable.
   */
  it("starts collapsed, with the way to open it on screen", () => {
    renderGrid();

    expect(screen.queryByTestId("agentic-composer")).toBeNull();
    expect(screen.getByTestId("agentic-composer-collapsed")).toBeTruthy();
    expect(screen.getByTestId("agentic-composer-reopen")).toBeTruthy();
    expect(screen.getByTestId("pane-resizer-horizontal")).toBeTruthy();
    expect(
      screen
        .getByLabelText("Instruction for Mika")
        .closest('[aria-hidden="true"]')
        ?.classList.contains("hidden"),
    ).toBe(true);
  });

  it("dragging the seam to the bottom collapses an opened bar to a strip", () => {
    window.localStorage.setItem(HEIGHT_KEY, OPEN);
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

  it("keeps a draft when the prompt bar is collapsed and reopened", () => {
    window.localStorage.setItem(HEIGHT_KEY, OPEN);
    renderGrid();
    const editor = screen.getByLabelText("Instruction for Mika");
    fireEvent.change(editor, { target: { value: "Keep this draft" } });

    dragSeam(700, 1400);
    fireEvent.click(screen.getByTestId("agentic-composer-reopen"));

    expect(
      (screen.getByLabelText("Instruction for Mika") as HTMLTextAreaElement).value,
    ).toBe("Keep this draft");
  });

  it("the reopen button brings the prompt bar back at its designed height", () => {
    renderGrid();
    expect(screen.getByTestId("agentic-composer-collapsed")).toBeTruthy();

    fireEvent.click(screen.getByTestId("agentic-composer-reopen"));

    const composer = screen.getByTestId("agentic-composer");
    expect(composer.style.height).toBe(`${OPEN}px`);
    expect(screen.getByLabelText(/instruction for Mika/i)).toBeTruthy();
  });

  it("remembers an opened bar across a remount", () => {
    // The stored height is the whole reason the default can be "shut": someone
    // who wants the writing surface opens it once and keeps it.
    renderGrid();
    fireEvent.click(screen.getByTestId("agentic-composer-reopen"));
    expect(window.localStorage.getItem(HEIGHT_KEY)).toBe(OPEN);

    cleanup();
    renderGrid();
    expect(screen.getByTestId("agentic-composer")).toBeTruthy();
  });

  it("remembers a collapsed bar across a remount", () => {
    window.localStorage.setItem(HEIGHT_KEY, OPEN);
    renderGrid();
    dragSeam(700, 1400);
    expect(window.localStorage.getItem(HEIGHT_KEY)).toBe(SHUT);

    cleanup();
    renderGrid();
    expect(screen.getByTestId("agentic-composer-collapsed")).toBeTruthy();
  });

  /*
   * Double-click TOGGLES rather than resets.
   *
   * `reset` means "back to the default", and the default is now the shut strip
   * — so wiring the seam to it would give a closed bar a double-click that
   * visibly does nothing, which reads as a dead control.
   */
  it("double-clicking the seam opens a shut bar and shuts an open one", () => {
    renderGrid();

    fireEvent.doubleClick(screen.getByTestId("pane-resizer-horizontal"));
    expect(screen.getByTestId("agentic-composer").style.height).toBe(`${OPEN}px`);

    fireEvent.doubleClick(screen.getByTestId("pane-resizer-horizontal"));
    expect(screen.getByTestId("agentic-composer-collapsed")).toBeTruthy();
  });
});


/*
 * Sizing the workspace by hand.
 *
 * Two complaints, one cause: panes were sized by CSS grid tracks, so they were
 * always all the same. Splitting one therefore resized the whole line, and the
 * boundaries between them were plain borders with nothing to grab.
 */
describe("resizing the workspace", () => {
  /** jsdom measures nothing, and a drag is pixels — so give it a size. */
  function measured(width: number, height: number) {
    const spies = [
      vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(width),
      vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(height),
    ];
    return () => spies.forEach((spy) => spy.mockRestore());
  }

  function widthOf(name: string): number {
    const style = screen.getByTestId(`pane-cell-${name}`).getAttribute("style") ?? "";
    const match = /width: (?:calc\()?([-\d.]+)%/.exec(style);
    if (!match) throw new Error(`${name} has no width: ${style}`);
    return Math.round(Number(match[1]) * 10) / 10;
  }

  /** The tree the grid last posted to the backend, or null. */
  function savedTree(): LayoutNode | null {
    const calls = vi.mocked(api.saveLayoutWeights).mock.calls;
    return calls.length > 0 ? (calls[calls.length - 1][0] as LayoutNode) : null;
  }

  /** Root-level weights of the last posted tree, rounded for assertions. */
  function savedRootWeights(): number[] | null {
    const tree = savedTree();
    if (!tree || !("children" in tree)) return null;
    return tree.weights.map((weight) => Math.round(weight * 100) / 100);
  }

  /*
   * Drag a seam from `fromX` to `toX`.
   *
   * Spelled with `MouseEvent` rather than `fireEvent.pointerDown` because jsdom
   * has no `PointerEvent`: the synthesised one arrives with no `clientX` at all,
   * so the drag reads a NaN distance and the test passes or fails for a reason
   * that has nothing to do with the code. A mouse event carries real
   * coordinates, and the listeners only ever look at the event's name.
   */
  function dragSeamBy(testId: string, fromX: number, toX: number) {
    const seam = screen.getByTestId(testId);
    // One `act` per step, not one around all three: the window listeners are
    // armed by an effect that runs when the drag STARTS, and a single block
    // would deliver the move before that effect had a chance to run.
    act(() => {
      seam.dispatchEvent(
        new MouseEvent("pointerdown", { clientX: fromX, bubbles: true }),
      );
    });
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { clientX: toX }));
    });
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup"));
    });
  }

  /** The same, down the other axis — a horizontal seam reads `clientY`. */
  function dragSeamDownBy(testId: string, fromY: number, toY: number) {
    const seam = screen.getByTestId(testId);
    act(() => {
      seam.dispatchEvent(
        new MouseEvent("pointerdown", { clientY: fromY, bubbles: true }),
      );
    });
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { clientY: toY }));
    });
    act(() => {
      window.dispatchEvent(new MouseEvent("pointerup"));
    });
  }

  /** Is a toolbar control currently refusing clicks? */
  function disabled(testId: string): boolean {
    return (screen.getByTestId(testId) as HTMLButtonElement).disabled;
  }

  it("puts a grab handle on every boundary between panes", () => {
    renderGrid(sessionWith([["Mika", 0], ["Nova", 0, 1], ["Aria", 1]]));
    // One between the two columns (a root boundary), one between the panes
    // stacked in the first column (a boundary inside child 0).
    expect(screen.getByTestId("pane-seam-root:1")).toBeTruthy();
    expect(screen.getByTestId("pane-seam-0:1")).toBeTruthy();
  });

  it("offers one seam per boundary the USER made, and no others", () => {
    // A crowded workspace used to grow an extra horizontal seam per wrap, for a
    // row boundary nobody asked for. Twelve columns are one line now, so the
    // eleven seams between them are the only ones there are.
    const panes = Array.from({ length: 12 }, (_, i) => [`T${i + 1}`, i] as [string, number]);
    renderGrid(sessionWith(panes));
    expect(screen.queryAllByTestId(/^pane-seam-/)).toHaveLength(11);
    expect(screen.queryAllByTestId(/^pane-seam-band:/)).toHaveLength(0);
  });

  it("moves width between the two panes a seam divides and no others", () => {
    const restore = measured(1800, 600);
    try {
      renderGrid(sessionWith([["Mika", 0], ["Nova", 1], ["Aria", 2]]));
      dragSeamBy("pane-seam-root:1", 600, 900);

      // A sixth of the line moved from Nova to Mika...
      expect(widthOf("Mika")).toBe(50);
      expect(widthOf("Nova")).toBe(16.7);
      // ...and the pane that was not part of the drag is untouched.
      expect(widthOf("Aria")).toBe(33.3);
    } finally {
      restore();
    }
  });

  /** Begin a drag and leave the pointer down, so the gesture can be inspected. */
  function holdSeam(testId: string, fromX: number) {
    act(() => {
      screen
        .getByTestId(testId)
        .dispatchEvent(new MouseEvent("pointerdown", { clientX: fromX, bubbles: true }));
    });
  }

  /**
   * Let one animation frame pass.
   *
   * A drag paints on FRAMES, not on pointer events — that is the whole point of
   * it (see `usePaneWeights`), so a test that moves the pointer and looks
   * immediately would be reading the frame before the one it caused.
   */
  async function flushFrame() {
    await act(async () => {
      await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
    });
  }

  /*
   * The panes follow the pointer WHILE it is down.
   *
   * This is the difference between a boundary you drag and one that jumps when
   * you let go, and it has to survive the thing that makes it fast: the frames
   * of a drag are painted straight onto the elements, and state is written once
   * at the end. Nothing about that may be visible from the outside — so the
   * width is checked mid-gesture, before any commit could have happened.
   */
  it("moves the panes while the pointer is still down, not on release", async () => {
    const restore = measured(1000, 600);
    try {
      renderGrid(sessionWith([["Mika", 0], ["Nova", 1]]));
      holdSeam("pane-seam-root:1", 500);
      act(() => {
        window.dispatchEvent(new MouseEvent("pointermove", { clientX: 750 }));
      });
      await flushFrame();

      expect(widthOf("Mika")).toBe(75);
      // ...and nothing has been committed or posted yet: a drag that wrote
      // per frame is what re-rendered every terminal on every pointer move.
      expect(api.saveLayoutWeights).not.toHaveBeenCalled();

      act(() => window.dispatchEvent(new MouseEvent("pointerup")));

      // The release keeps exactly what was on screen — no snap back to the
      // frame before it, and no second jump.
      expect(widthOf("Mika")).toBe(75);
      // The result reaches the backend (debounced), so the sizes survive a
      // restart on any machine and come back with a resumed workspace.
      await waitFor(() => expect(savedRootWeights()).toEqual([1.5, 0.5]));
    } finally {
      restore();
    }
  });

  /*
   * The frames of a drag do not re-render the terminals.
   *
   * This is the fix itself, stated as a test. A pane is a live terminal with a
   * header, a recap card and a scrollbar; re-rendering a dozen of them on every
   * pointer move is what dropped the gesture to a few frames a second, and it
   * fed back — each render resized every pane's box, which woke each pane's own
   * observer, which refitted the terminal and made the agent behind it redraw.
   * So the boxes are painted directly and React is told once, at the end.
   */
  it("does not re-render the terminals for the frames of a drag", async () => {
    const restore = measured(1000, 600);
    try {
      renderGrid(sessionWith([["Mika", 0], ["Nova", 1]]));
      // Let the mount-time recap poll land first — its answer re-renders every
      // pane once, and counting that as a frame of the drag would be measuring
      // the test's own setup.
      await flushFrame();
      holdSeam("pane-seam-root:1", 500);
      const before = paneRenders.get("Mika") ?? 0;

      for (const x of [560, 620, 680, 750]) {
        act(() => window.dispatchEvent(new MouseEvent("pointermove", { clientX: x })));
        await flushFrame();
      }

      // Four frames of movement, visible on screen...
      expect(widthOf("Mika")).toBe(75);
      // ...and not one render of the pane behind it.
      expect(paneRenders.get("Mika")).toBe(before);

      act(() => window.dispatchEvent(new MouseEvent("pointerup")));
      // Letting go is what costs renders: the sizes go into state and the panes
      // are told the workspace has stopped moving. A couple of commits for the
      // whole gesture, rather than one per frame — that is the difference.
      expect(paneRenders.get("Mika")).toBeLessThanOrEqual(before + 2);
    } finally {
      restore();
    }
  });

  /*
   * A pane whose size is mid-gesture does not resize its terminal.
   *
   * Refitting means telling the agent behind the pane to redraw its whole
   * screen, and during a drag that answer is stale before it arrives — sixty
   * times a second, on the thread drawing the drag.
   */
  it("tells the panes to hold still while a seam is moving, and to catch up after", () => {
    const restore = measured(1000, 600);
    try {
      renderGrid(sessionWith([["Mika", 0], ["Nova", 1]]));
      const busy = () => screen.getByTestId("pane-Mika").getAttribute("data-layout-busy");
      expect(busy()).toBe("no");

      holdSeam("pane-seam-root:1", 500);
      expect(busy()).toBe("yes");

      act(() => window.dispatchEvent(new MouseEvent("pointerup")));
      expect(busy()).toBe("no");
    } finally {
      restore();
    }
  });

  /* The prompt bar changes every pane's height, so it counts as the same kind
     of motion — a pane must not refit for each frame of that drag either. */
  it("counts a prompt-bar drag as the workspace moving too", () => {
    renderGrid(sessionWith([["Mika", 0], ["Nova", 1]]));
    const busy = () => screen.getByTestId("pane-Mika").getAttribute("data-layout-busy");

    fireEvent.pointerDown(screen.getByTestId("pane-resizer-horizontal"), { clientY: 700 });
    expect(busy()).toBe("yes");

    act(() => window.dispatchEvent(new MouseEvent("pointerup")));
    expect(busy()).toBe("no");
  });

  it("posts the sizes so a restart brings the workspace back as it was", async () => {
    const restore = measured(1000, 600);
    try {
      renderGrid(sessionWith([["Mika", 0], ["Nova", 1]]));
      dragSeamBy("pane-seam-root:1", 500, 750);
      // The whole tree travels; the backend adopts its weights and persists
      // them in the resume snapshot — no browser storage involved any more.
      await waitFor(() => expect(savedRootWeights()).toEqual([1.5, 0.5]));
    } finally {
      restore();
    }
  });

  it("evens two neighbours out again on a double-click", () => {
    const restore = measured(1000, 600);
    try {
      renderGrid(sessionWith([["Mika", 0], ["Nova", 1]]));
      dragSeamBy("pane-seam-root:1", 500, 750);
      expect(widthOf("Mika")).toBe(75);

      fireEvent.doubleClick(screen.getByTestId("pane-seam-root:1"));
      expect(widthOf("Mika")).toBe(50);
    } finally {
      restore();
    }
  });

  /*
   * The toolbar's "even them out" button.
   *
   * The request it answers (2026-08-04): after an hour of dragging, a wall of
   * terminals is five different widths and straightening it by hand means
   * dragging every seam back one at a time. The one thing it must NEVER do is
   * rearrange anything — which pane sits in which column, and which pane is
   * stacked under which, is exactly what the user asked to keep.
   */
  it("evens every terminal out on one click", async () => {
    const restore = measured(1800, 900);
    try {
      renderGrid(
        sessionWith([
          ["Mika", 0],
          ["Nova", 1],
          ["Vega", 1, 1],
          ["Aria", 2],
        ]),
      );
      // Out of shape on BOTH axes: one column dragged wide, and the stack
      // inside another column dragged so its two panes are unequal.
      dragSeamBy("pane-seam-root:1", 600, 900);
      dragSeamDownBy("pane-seam-1:1", 450, 700);
      expect(widthOf("Mika")).not.toBe(33.3);
      expect(Math.round(box("Vega").height)).not.toBe(50);

      fireEvent.click(screen.getByTestId("agentic-even-panes"));

      // Every column the same width...
      expect(widthOf("Mika")).toBe(33.3);
      expect(widthOf("Nova")).toBe(33.3);
      expect(widthOf("Aria")).toBe(33.3);
      // ...and the two panes sharing a column splitting its height equally.
      expect(box("Nova").height).toBeCloseTo(50, 3);
      expect(box("Vega").height).toBeCloseTo(50, 3);
      // Posted, so the workspace comes back straight rather than snapping to
      // the pre-click sizes on the next mount.
      await waitFor(() => {
        const tree = savedTree();
        expect(tree && "weights" in tree && tree.weights).toEqual([1, 1, 1]);
      });
    } finally {
      restore();
    }
  });

  it("moves no pane while it evens the sizes out", () => {
    const restore = measured(1800, 900);
    try {
      renderGrid(
        sessionWith([
          ["Mika", 0],
          ["Nova", 1],
          ["Vega", 1, 1],
          ["Aria", 2],
        ]),
      );
      dragSeamBy("pane-seam-root:1", 600, 900);

      fireEvent.click(screen.getByTestId("agentic-even-panes"));

      // Same left-to-right order as before, Vega still stacked under Nova in
      // the middle column, and nothing pushed onto another line.
      expect(box("Mika").left).toBe(0);
      expect(box("Nova").left).toBeCloseTo(33.333, 2);
      expect(box("Aria").left).toBeCloseTo(66.667, 2);
      expect(box("Vega").left).toBe(box("Nova").left);
      expect(box("Nova").top).toBe(0);
      expect(box("Vega").top).toBeCloseTo(50, 3);
    } finally {
      restore();
    }
  });

  it("offers nothing to press while the workspace is already even", () => {
    // A live button that would change nothing is a button people press twice
    // and then distrust.
    const restore = measured(1000, 600);
    try {
      renderGrid(sessionWith([["Mika", 0], ["Nova", 1]]));
      expect(disabled("agentic-even-panes")).toBe(true);

      dragSeamBy("pane-seam-root:1", 500, 750);
      expect(disabled("agentic-even-panes")).toBe(false);

      fireEvent.click(screen.getByTestId("agentic-even-panes"));
      expect(disabled("agentic-even-panes")).toBe(true);
    } finally {
      restore();
    }
  });

  it("stands down while one pane covers the others", () => {
    // A maximized workspace has no boundaries on screen, so evening them out
    // would be a click with nothing to show for it.
    const restore = measured(1000, 600);
    try {
      renderGrid(sessionWith([["Mika", 0], ["Nova", 1]]));
      dragSeamBy("pane-seam-root:1", 500, 750);
      fireEvent.click(screen.getByTestId("pane-maximize-Mika"));
      expect(disabled("agentic-even-panes")).toBe(true);

      fireEvent.click(screen.getByTestId("pane-maximize-Mika"));
      expect(disabled("agentic-even-panes")).toBe(false);
    } finally {
      restore();
    }
  });

  it("moves a seam from the keyboard, which is the only way back on a touchpad", () => {
    const restore = measured(1000, 600);
    try {
      renderGrid(sessionWith([["Mika", 0], ["Nova", 1]]));
      const seam = screen.getByTestId("pane-seam-root:1");
      fireEvent.keyDown(seam, { key: "ArrowRight", shiftKey: true });
      // One coarse step of 64 px out of 1000, moved from Nova to Mika.
      expect(widthOf("Mika")).toBe(56.4);
    } finally {
      restore();
    }
  });

  it("draws the halved anchor the backend's tree describes after a split", async () => {
    // The halving itself is the backend's (`layout_tree.split_pane`, covered
    // there); the grid's job is to DRAW the tree the response carries instead
    // of second-guessing it with its own weights.
    const next = sessionWith([["Mika", 0], ["New", 1], ["Nova", 2]], {
      direction: "row",
      children: [{ pane: "mika" }, { pane: "new" }, { pane: "nova" }],
      weights: [0.5, 0.5, 1],
    });
    vi.mocked(api.addTerminal).mockResolvedValue(next);
    const { onSessionChanged, rerender } = renderGrid(
      sessionWith([["Mika", 0], ["Nova", 1]]),
    );

    fireEvent.click(screen.getByTestId("pane-split-right-Mika"));
    await waitFor(() => expect(onSessionChanged).toHaveBeenCalledWith(next));
    // The view owns the session prop, so the response reaches the grid the
    // way it does in the app: as a new prop.
    rerender({ session: next });

    expect(widthOf("Mika")).toBe(25);
    expect(widthOf("New")).toBe(25);
    expect(widthOf("Nova")).toBe(50);
  });

  /*
   * The workspace changes WITHOUT this grid doing it.
   *
   * A terminal opened by voice, closed by another client, or the backend
   * resuming after a restart arrives here as nothing but a new `session`
   * prop. The tree in it is the whole answer now — including the sizes, which
   * the backend carries per pane KEY, so nothing here has to guess which pane
   * a dragged width belonged to (the index-keyed failure of 2026-07-31).
   */
  it("follows the authoritative tree when the workspace changes from outside", async () => {
    const restore = measured(1800, 600);
    try {
      const { rerender } = renderGrid(
        sessionWith([["Mika", 0], ["Nova", 1], ["Aria", 2]]),
      );
      dragSeamBy("pane-seam-root:2", 1200, 900);
      expect(widthOf("Aria")).toBe(50);

      // Mika was closed elsewhere. The backend dissolved its share and kept
      // Nova's and Aria's dragged weights — the new tree says all of that.
      rerender({
        session: sessionWith([["Nova", 0], ["Aria", 1]], {
          direction: "row",
          children: [{ pane: "nova" }, { pane: "aria" }],
          weights: [0.5, 1.5],
        }),
      });

      expect(widthOf("Aria")).toBe(75);
      expect(widthOf("Nova")).toBe(25);
    } finally {
      restore();
    }
  });

  it("keeps the dragged sizes when the same shape comes back with older weights", async () => {
    // The session prop catches up on its own schedule (a poll, an event), and
    // what it echoes may predate the drag that just happened. As long as the
    // SHAPE matches, the local sizes are at least as fresh — snapping back to
    // the server's would make every drag twitch a moment later.
    const restore = measured(1000, 600);
    try {
      const { rerender } = renderGrid(sessionWith([["Mika", 0], ["Nova", 1]]));
      dragSeamBy("pane-seam-root:1", 500, 750);
      expect(widthOf("Mika")).toBe(75);

      rerender({ session: sessionWith([["Mika", 0], ["Nova", 1]]) });

      expect(widthOf("Mika")).toBe(75);
    } finally {
      restore();
    }
  });

  it("lets a structural change win over a drag it interrupted", async () => {
    const restore = measured(1800, 600);
    try {
      const { rerender } = renderGrid(
        sessionWith([["Mika", 0], ["Nova", 1], ["Aria", 2]]),
      );
      // Grab a seam and, while the pointer is down, let the workspace change
      // under the gesture: the drag's tree describes panes that are partly
      // gone, so on release the server's NEW tree is the only honest layout.
      holdSeam("pane-seam-root:1", 600);
      rerender({
        session: sessionWith([["Nova", 0], ["Aria", 1]], {
          direction: "row",
          children: [{ pane: "nova" }, { pane: "aria" }],
          weights: [0.5, 1.5],
        }),
      });
      act(() => window.dispatchEvent(new MouseEvent("pointerup")));

      expect(widthOf("Aria")).toBe(75);
      expect(widthOf("Nova")).toBe(25);
    } finally {
      restore();
    }
  });

  it("does not read a rename as a rearrangement", async () => {
    // The tree references panes by KEY, and a rename leaves the key alone —
    // so the renamed pane keeps the width that was dragged for it.
    const renamed = sessionWith([["Frontend", 0], ["Nova", 1]]);
    renamed.terminals[0].key = "mika";
    renamed.layout = {
      direction: "row",
      children: [{ pane: "mika" }, { pane: "nova" }],
      weights: [1, 1],
    };
    vi.mocked(api.renameTerminal).mockResolvedValue(renamed);
    const restore = measured(1000, 600);
    try {
      const { rerender } = renderGrid(sessionWith([["Mika", 0], ["Nova", 1]]));
      dragSeamBy("pane-seam-root:1", 500, 750);
      expect(widthOf("Mika")).toBe(75);

      fireEvent.click(screen.getByTestId("pane-rename-Mika"));
      await waitFor(() =>
        expect(api.renameTerminal).toHaveBeenCalledWith("Mika", "Frontend"),
      );
      rerender({ session: renamed });

      expect(widthOf("Frontend")).toBe(75);
    } finally {
      restore();
    }
  });
});

/**
 * The standing rule for this screen, reported 2026-08-04: the workspace is ONE
 * screenful, always. Opening a seventh terminal used to widen the canvas past
 * the window, so the maintainer had to scroll sideways to see the pane they had
 * just opened — and watching eight agents at once meant scrolling between them.
 * Panes get smaller instead.
 */
describe("a workspace with far more panes than the window comfortably fits", () => {
  /** Many panes, one per column, the way a big fan-out opens them. */
  function manyPanes(count: number) {
    return sessionWith(
      Array.from({ length: count }, (_, i) => [`T${i}`, i] as [string, number]),
    );
  }

  it("divides the window's HEIGHT between a deep column instead of growing past it", () => {
    // The canvas used to be drawn 4 × 240 px tall for a four-deep column, and
    // the grid scrolled down to the rest.
    renderGrid(
      sessionWith(
        Array.from({ length: 4 }, (_, i) => [`T${i}`, 0, i] as [string, number, number]),
      ),
    );
    const canvas = screen.getByTestId("agentic-grid-canvas");
    expect(canvas.style.height).toBe("");
    expect(canvas.className).toContain("h-full");
    // Four panes, four quarters, the last one ending exactly at the bottom.
    expect(box("T3")).toMatchObject({ top: 75, height: 25 });
  });

  it("divides the window's WIDTH between forty columns instead of growing past it", () => {
    // The 2026-08-03 half of the same rule, which used to draw the canvas
    // 40 × 380 px wide and scroll sideways.
    renderGrid(manyPanes(40));
    const canvas = screen.getByTestId("agentic-grid-canvas");
    expect(canvas.style.width).toBe("");
    expect(canvas.className).toContain("w-full");
    expect(box("T39").left).toBeCloseTo(97.5, 5);
  });

  it("never scrolls on either axis, however many panes are open", () => {
    renderGrid(manyPanes(40));
    const grid = screen.getByTestId("agentic-grid").className;
    expect(grid).toContain("overflow-hidden");
    expect(grid).not.toContain("overflow-auto");
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

  /**
   * The grid outlives the section being on screen (see MainView): it is hidden
   * rather than unmounted so its terminals survive a trip to another view. That
   * makes "is anyone looking?" a real question — `/recaps` walks every pane's
   * replay buffer through the summarizer, every five seconds, on the same event
   * loop that carries the wake microphone.
   */
  it("asks nothing at all while its section is off screen", async () => {
    renderGrid(BASE, { onScreen: false });
    // The read on mount is the poller's first act, and the interval is created
    // beside it — so no read here means no interval either, and a hidden
    // workspace costs the backend nothing.
    await act(async () => {});

    expect(api.fetchTerminalRecaps).not.toHaveBeenCalled();
  });

  it("catches its headers up the moment it comes back", async () => {
    const { rerender } = renderGrid(BASE, { onScreen: false });
    await act(async () => {});
    expect(api.fetchTerminalRecaps).not.toHaveBeenCalled();

    // Not merely "resumes polling" — reads AT ONCE. A grid that spent five
    // minutes hidden has headers five minutes old, and waiting out another
    // interval before correcting them is why this is asserted separately.
    rerender({ onScreen: true });
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

  it("asks the fast activity feed too, and wears its answer on the badge", async () => {
    // The badge has its own poll beside the recap one — one stamped word per
    // pane, quick enough that a pane that starts working is SEEN to start
    // working rather than reported a recap-interval later.
    vi.mocked(api.fetchTerminalActivity).mockResolvedValue({
      workspace_id: "ide_test",
      terminals: [
        {
          key: "mika",
          name: "Mika",
          status: "live",
          activity: "working",
          activity_since: 0,
          worked: true,
        },
      ],
    });

    renderGrid();

    await waitFor(() =>
      expect(api.fetchTerminalActivity).toHaveBeenCalledWith("ide_test"),
    );
    await waitFor(() => {
      const badges = screen.getAllByTestId("pane-activity");
      expect(badges.some((badge) => badge.dataset.activity === "working")).toBe(true);
    });
  });
});

/*
 * Rearranging the grid by dragging a pane by its header.
 *
 * A workspace is assembled one split at a time and ends up in the order the
 * splits happened, not the order the work is in. The only way to fix that used
 * to be closing a pane and opening a new one elsewhere, which kills a working
 * agent and its whole conversation — so these tests are really about one
 * promise: a drop changes where a pane is drawn and NOTHING else.
 *
 * jsdom ships neither PointerEvent nor layout, so both are supplied here: the
 * gesture is fired as MouseEvents (which carry the coordinates a plain Event
 * drops), and each pane cell is told where it is on screen.
 */
describe("rearranging panes", () => {
  /** Give a pane cell a box, since jsdom lays nothing out. */
  function placeCell(name: string, left: number, top: number, width = 200, height = 100) {
    const cell = screen.getByTestId(`pane-cell-${name}`);
    cell.getBoundingClientRect = () =>
      ({
        left,
        top,
        width,
        height,
        right: left + width,
        bottom: top + height,
        x: left,
        y: top,
        toJSON: () => ({}),
      }) as DOMRect;
  }

  /** Two panes side by side, each 200×100, Mika at the origin. */
  function twoPlacedPanes() {
    const handles = renderGrid();
    placeCell("Mika", 0, 0);
    placeCell("Nova", 200, 0);
    return handles;
  }

  function press(name: string, x: number, y: number) {
    fireEvent(
      screen.getByTestId(`pane-drag-${name}`),
      new MouseEvent("pointerdown", {
        bubbles: true,
        clientX: x,
        clientY: y,
        button: 0,
      }),
    );
  }

  function move(x: number, y: number) {
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { clientX: x, clientY: y }));
    });
  }

  /** Let go — awaited, so the move the drop triggers settles inside `act`. */
  async function release() {
    await act(async () => {
      window.dispatchEvent(new MouseEvent("pointerup"));
    });
  }

  it("MOVES a pane past another one rather than exchanging the two", async () => {
    // The whole point of dragging: one pane goes where it was dropped. A swap
    // would send Nova back the other way, which nobody asked for (BUG-111).
    const moved = sessionWith([
      ["Nova", 0],
      ["Mika", 1],
    ]);
    vi.mocked(api.moveTerminal).mockResolvedValue(moved);
    const { onSessionChanged } = twoPlacedPanes();

    press("Mika", 100, 50);
    move(380, 50); // carried well into Nova's right half

    // Mid-drag the grid says what the drop would do, before it happens.
    expect(screen.getByTestId("pane-dropzone-Nova").dataset.zone).toBe("right");
    expect(screen.getByTestId("agentic-arrange-ghost").textContent).toContain("Mika");
    expect(screen.getByTestId("pane-Mika").dataset.arranging).toBe("yes");

    await release();

    expect(api.moveTerminal).toHaveBeenCalledWith("Mika", "Nova", "right");
    await waitFor(() => expect(onSessionChanged).toHaveBeenCalledWith(moved));
  });

  it("lands on the half of the target the pointer is in", async () => {
    vi.mocked(api.moveTerminal).mockResolvedValue(BASE);
    twoPlacedPanes();

    press("Mika", 100, 50);
    move(240, 50); // Nova's left half

    expect(screen.getByTestId("pane-dropzone-Nova").dataset.zone).toBe("left");

    await release();

    expect(api.moveTerminal).toHaveBeenCalledWith("Mika", "Nova", "left");
  });

  it("swaps two panes when the drop is made with Shift held", async () => {
    // Exchanging two panes is still worth having — it is the only move that
    // leaves the grid's shape untouched — so it lives on the modifier, where it
    // cannot happen to someone who did not ask for it.
    const swapped = sessionWith([
      ["Nova", 0],
      ["Mika", 1],
    ]);
    vi.mocked(api.moveTerminal).mockResolvedValue(swapped);
    twoPlacedPanes();

    press("Mika", 100, 50);
    move(380, 50);
    expect(screen.getByTestId("pane-dropzone-Nova").dataset.zone).toBe("right");
    expect(screen.getByTestId("agentic-arrange-swap-hint")).toBeTruthy();

    // The preview answers the modifier without the pointer moving at all.
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Shift" }));
    });
    expect(screen.getByTestId("pane-dropzone-Nova").dataset.zone).toBe("swap");
    expect(screen.queryByTestId("agentic-arrange-swap-hint")).toBeNull();

    await release();

    expect(api.moveTerminal).toHaveBeenCalledWith("Mika", "Nova", "swap");
  });

  it("goes back to moving when Shift is let go mid-drag", async () => {
    vi.mocked(api.moveTerminal).mockResolvedValue(BASE);
    twoPlacedPanes();

    press("Mika", 100, 50);
    move(380, 50);
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Shift" }));
    });
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keyup", { key: "Shift" }));
    });
    expect(screen.getByTestId("pane-dropzone-Nova").dataset.zone).toBe("right");

    await release();

    expect(api.moveTerminal).toHaveBeenCalledWith("Mika", "Nova", "right");
  });

  it("reads the bottom of a pane as 'put it underneath'", async () => {
    vi.mocked(api.moveTerminal).mockResolvedValue(BASE);
    twoPlacedPanes();

    press("Mika", 100, 50);
    move(300, 97);
    await release();

    expect(api.moveTerminal).toHaveBeenCalledWith("Mika", "Nova", "below");
  });

  it("leaves a plain click on the header alone", async () => {
    // The header is also where a pane is clicked to focus it. A press that goes
    // nowhere must stay a click, or every click would rearrange the grid.
    twoPlacedPanes();

    press("Mika", 100, 50);
    move(102, 51); // inside the threshold — not a drag
    await release();

    expect(api.moveTerminal).not.toHaveBeenCalled();
    expect(screen.queryByTestId("agentic-arrange-ghost")).toBeNull();
  });

  it("cancels the drag on Escape", async () => {
    twoPlacedPanes();

    press("Mika", 100, 50);
    move(300, 50);
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    await release();

    expect(api.moveTerminal).not.toHaveBeenCalled();
    expect(screen.queryByTestId("agentic-arrange-ghost")).toBeNull();
  });

  it("does nothing when a pane is dropped back on itself", async () => {
    twoPlacedPanes();

    press("Mika", 100, 50);
    move(150, 50); // still over Mika
    await release();

    expect(api.moveTerminal).not.toHaveBeenCalled();
  });

  it("marks the pane that landed, so the move is SEEN", async () => {
    // A committed move used to repaint in one frame with nothing pointing at
    // the pane that travelled — among near-identical panes that read as
    // "dragging is broken" (reported 2026-08-07). The carried pane wears the
    // arrival ring for a moment after the drop.
    const moved = sessionWith([
      ["Nova", 0],
      ["Mika", 1],
    ]);
    vi.mocked(api.moveTerminal).mockResolvedValue(moved);
    twoPlacedPanes();

    press("Mika", 100, 50);
    move(380, 50);
    await release();

    await waitFor(() =>
      expect(screen.getByTestId("pane-cell-Mika").className).toContain("ring-2"),
    );
  });

  it("says so when a drop changes nothing", async () => {
    // "Right of Nova" is a legal drop for the pane already sitting right of
    // Nova. The backend answers with the unchanged workspace — and the grid
    // used to answer with silence, indistinguishable from a broken drag. Now
    // the silence is replaced by a plain answer, and no ring pretends a move.
    vi.mocked(api.moveTerminal).mockResolvedValue(BASE);
    const { onSessionChanged } = twoPlacedPanes();

    press("Mika", 100, 50);
    move(380, 50);
    await release();

    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith("info", expect.stringContaining("already")),
    );
    expect(onSessionChanged).not.toHaveBeenCalled();
    expect(screen.getByTestId("pane-cell-Mika").className).not.toContain("ring-2");
  });

  it("offers no drag while a pane is maximized", () => {
    // Every other pane is hidden, so there is nothing on screen to drop onto.
    renderGrid();
    expect(screen.getByTestId("pane-Mika").dataset.arrangeable).toBe("yes");

    fireEvent.click(screen.getByTestId("pane-maximize-Mika"));

    expect(screen.getByTestId("pane-Mika").dataset.arrangeable).toBe("no");
  });

  it("offers no drag in selection mode", () => {
    // Selection mode's overlay owns every click on a pane.
    renderGrid();

    fireEvent.click(screen.getByTestId("terminal-selection-toggle"));

    expect(screen.getByTestId("pane-Mika").dataset.arrangeable).toBe("no");
  });

  it("says so when the move is refused", async () => {
    vi.mocked(api.moveTerminal).mockRejectedValue(new Error("Nova is gone."));
    const { onSessionChanged } = twoPlacedPanes();

    press("Mika", 100, 50);
    move(300, 50);
    await release();

    await waitFor(() => expect(pushToast).toHaveBeenCalledWith("error", "Nova is gone."));
    expect(onSessionChanged).not.toHaveBeenCalled();
  });
});

/*
 * The one header row, and what shares it.
 *
 * Three horizontal bands used to sit above the panes: the app's bar, the
 * workspace tabs, and this toolbar. They are one row now, which is only safe as
 * long as the things that moved into it are actually IN it — a Restart button
 * that quietly stopped rendering would leave the section with no way to pick up
 * its own rebuild, and nothing else on the screen would look wrong.
 */
describe("the workspace header row", () => {
  it("carries the workspace tabs beside this workspace's own controls", () => {
    renderGrid(BASE, {
      workspaceBar: <div data-testid="stand-in-tabs">tabs</div>,
    });

    const toolbar = screen.getByTestId("agentic-toolbar");
    expect(toolbar.contains(screen.getByTestId("stand-in-tabs"))).toBe(true);
    expect(toolbar.contains(screen.getByTestId("agentic-focus-toggle"))).toBe(true);
  });

  it("carries the app's own actions in the same row", () => {
    renderGrid(BASE, {
      appActions: <button type="button">Restart</button>,
    });

    const toolbar = screen.getByTestId("agentic-toolbar");
    expect(
      toolbar.contains(screen.getByRole("button", { name: "Restart" })),
    ).toBe(true);
  });

  it("keeps crowded toolbar controls in one narrow-window overflow panel", () => {
    renderGrid(BASE, {
      workspaceBar: <div data-testid="stand-in-tabs">tabs</div>,
      appActions: <button type="button">Restart</button>,
    });

    const trigger = screen.getByTestId("agentic-toolbar-overflow");
    fireEvent.click(trigger);
    const panel = screen.getByTestId("agentic-toolbar-overflow-panel");
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(panel.contains(screen.getByTestId("agentic-focus-toggle"))).toBe(true);
    expect(panel.contains(screen.getByRole("button", { name: "Restart" }))).toBe(true);
    expect(
      (screen.getByRole("button", {
        name: "Close workspace controls",
      }) as HTMLButtonElement).tabIndex,
    ).toBe(-1);

    fireEvent.focus(screen.getByTestId("agentic-focus-toggle"));
    fireEvent.keyDown(panel, { key: "Escape" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(trigger);
  });

  it("names the project itself when no tabs were handed to it", () => {
    // The wizard and the component tests render the grid on its own. Without
    // the tabs the row would otherwise open with no idea which folder it is.
    renderGrid();

    expect(screen.getByTestId("agentic-toolbar").textContent).toContain("project");
  });

  it("expands the file explorer without remounting any live agent pane", async () => {
    renderGrid();

    const pane = screen.getByTestId("pane-Mika");
    const toggle = screen.getByTestId("workspace-explorer-toggle");
    fireEvent.click(toggle);

    expect(await screen.findByTestId("workspace-explorer")).toBeTruthy();
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("pane-Mika")).toBe(pane);
    expect(screen.getByTestId("agentic-grid").nextElementSibling).toBe(
      screen.getByTestId("workspace-explorer-host"),
    );

    fireEvent.click(screen.getByRole("button", { name: "Close the explorer" }));
    expect(screen.queryByTestId("workspace-explorer")).toBeNull();
    expect(screen.getByTestId("pane-Mika")).toBe(pane);
  });

  it("opens workspace files in-app without remounting a live agent pane", async () => {
    vi.mocked(api.fetchWorkspaceFiles).mockResolvedValueOnce({
      workspace_id: "ide_test",
      root_name: "project",
      path: "",
      entries: [
        {
          name: "README.md",
          path: "README.md",
          is_directory: false,
          is_symlink: false,
          size: 18,
        },
      ],
      truncated: false,
    });
    vi.mocked(api.fetchWorkspaceFilePreview).mockResolvedValueOnce({
      workspace_id: "ide_test",
      path: "README.md",
      name: "README.md",
      size: 18,
      media_type: "text/markdown",
      text: "# Inside the IDE",
      truncated: false,
      hex_preview: null,
    });
    renderGrid();

    const pane = screen.getByTestId("pane-Mika");
    fireEvent.click(screen.getByTestId("workspace-explorer-toggle"));
    const file = await screen.findByRole("treeitem", { name: /README\.md/i });
    fireEvent.click(file);

    expect(await screen.findByRole("heading", { name: "Inside the IDE" })).toBeTruthy();
    expect(screen.getByTestId("workspace-file-viewer")).toBeTruthy();
    expect(screen.getByTestId("pane-Mika")).toBe(pane);
    fireEvent.click(screen.getByRole("button", { name: "Close file preview" }));
    expect(screen.queryByTestId("workspace-file-viewer")).toBeNull();
    expect(screen.getByTestId("pane-Mika")).toBe(pane);
    await waitFor(() => expect(document.activeElement).toBe(file));
  });

  it("resizes the right-hand explorer from its left seam", async () => {
    renderGrid();
    fireEvent.click(screen.getByTestId("workspace-explorer-toggle"));
    await screen.findByTestId("workspace-explorer");

    const host = screen.getByTestId("workspace-explorer-host");
    const seam = screen.getByTestId("workspace-explorer-resizer");
    const pane = screen.getByTestId("pane-Mika");
    expect(host.style.width).toBe("280px");
    expect(seam.getAttribute("role")).toBe("separator");
    expect(seam.getAttribute("aria-orientation")).toBe("vertical");
    expect(seam.getAttribute("aria-controls")).toBe("workspace-explorer");
    expect(seam.getAttribute("aria-valuemin")).toBe("220");
    expect(seam.getAttribute("aria-valuemax")).toBe("640");
    expect(seam.getAttribute("aria-valuenow")).toBe("280");

    // The explorer sits to the RIGHT of this seam, so Left grows it and Right
    // shrinks it — the inverse of a left sidebar's grip.
    fireEvent.keyDown(seam, { key: "ArrowLeft" });
    expect(host.style.width).toBe("296px");
    expect(seam.getAttribute("aria-valuenow")).toBe("296");
    fireEvent.keyDown(seam, { key: "ArrowRight" });
    expect(host.style.width).toBe("280px");

    // jsdom has no PointerEvent constructor. A MouseEvent dispatched under the
    // pointer event's name carries the real clientX that the drag hook reads.
    act(() => {
      seam.dispatchEvent(
        new MouseEvent("pointerdown", { bubbles: true, clientX: 1_000 }),
      );
    });
    expect(pane.dataset.layoutBusy).toBe("yes");
    act(() => window.dispatchEvent(new MouseEvent("pointercancel")));
    expect(pane.dataset.layoutBusy).toBe("no");

    act(() => {
      seam.dispatchEvent(
        new MouseEvent("pointerdown", { bubbles: true, clientX: 1_000 }),
      );
    });
    act(() => {
      window.dispatchEvent(new MouseEvent("pointermove", { clientX: 900 }));
      window.dispatchEvent(new MouseEvent("pointerup"));
    });

    expect(host.style.width).toBe("380px");
    expect(pane.dataset.layoutBusy).toBe("no");
    expect(window.localStorage.getItem("jarvis.agenticIde.explorerWidth.v1")).toBe("380");

    fireEvent.doubleClick(seam);
    expect(host.style.width).toBe("280px");
  });

  it("keeps usable terminal space when a wide explorer preference meets a narrow frame", async () => {
    const width = vi
      .spyOn(HTMLElement.prototype, "clientWidth", "get")
      .mockReturnValue(760);
    window.localStorage.setItem("jarvis.agenticIde.explorerWidth.v1", "640");
    try {
      renderGrid();
      fireEvent.click(screen.getByTestId("workspace-explorer-toggle"));
      await waitFor(() =>
        expect(screen.getByTestId("workspace-explorer-host").style.width).toBe("440px"),
      );
      const seam = screen.getByTestId("workspace-explorer-resizer");
      expect(seam.getAttribute("aria-valuemax")).toBe("440");
      expect(seam.getAttribute("aria-valuenow")).toBe("440");
      expect(window.localStorage.getItem("jarvis.agenticIde.explorerWidth.v1")).toBe("640");
    } finally {
      width.mockRestore();
    }
  });
});

/**
 * Panes arrive from outside this grid — spoken across the room, or from the
 * CLI — so nothing draws the user's eye to the change. Reported 2026-07-28 as
 * terminals that "just don't load": they had loaded, unannounced (and, back
 * when a workspace could be taller than its viewport, off screen as well).
 */
describe("panes that appear from outside the grid", () => {
  it("marks what just arrived, without moving the workspace under the user", () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });

    const grid = (session: SessionState) => (
      <AgenticGrid
        session={session}
        focusMode={false}
        onToggleFocus={vi.fn()}
        onClose={vi.fn()}
        onSessionChanged={vi.fn()}
      />
    );

    const view = render(
      grid(
        sessionWith([
          ["T1", 0],
          ["T2", 1],
        ]),
      ),
    );

    view.rerender(
      grid(
        sessionWith([
          ["T1", 0],
          ["T2", 1],
          ["T3", 2],
          ["T4", 3],
        ]),
      ),
    );

    // Nothing is scrolled to any more: the workspace is one screenful, so the
    // new pane is already in view — and the only scroller such a call could
    // still find is the app's own, which would move the whole section.
    expect(scrollIntoView).not.toHaveBeenCalled();
    // The panes that arrived wear a ring; the ones that were already there
    // must not, or "what is new" says nothing at all.
    expect(screen.getByTestId("pane-cell-T4").className).toContain("ring-2");
    expect(screen.getByTestId("pane-cell-T3").className).toContain("ring-2");
    expect(screen.getByTestId("pane-cell-T1").className).not.toContain("ring-2");

    Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
  });
});

describe("renaming a pane", () => {
  /** The workspace as it comes back once "Mika" has become "Frontend". */
  const RENAMED = (() => {
    const next = sessionWith([
      ["Frontend", 0],
      ["Nova", 1],
    ]);
    // The key is what survives a rename — it is what the running terminal is
    // filed under — so the answer keeps Mika's.
    next.terminals[0] = { ...next.terminals[0], key: "mika" };
    return next;
  })();

  it("asks the backend and redraws from its answer", async () => {
    vi.mocked(api.renameTerminal).mockResolvedValue(RENAMED);
    const { onSessionChanged } = renderGrid();

    fireEvent.click(screen.getByTestId("pane-rename-Mika"));

    await waitFor(() =>
      expect(api.renameTerminal).toHaveBeenCalledWith("Mika", "Frontend"),
    );
    expect(onSessionChanged).toHaveBeenCalledWith(RENAMED);
  });

  it("carries the maximized pane across to its new name", async () => {
    vi.mocked(api.renameTerminal).mockResolvedValue(RENAMED);
    const { rerender } = renderGrid();

    fireEvent.click(screen.getByTestId("pane-maximize-Mika"));
    expect(screen.getByTestId("pane-Mika").getAttribute("data-maximized")).toBe("yes");

    fireEvent.click(screen.getByTestId("pane-rename-Mika"));
    await waitFor(() => expect(api.renameTerminal).toHaveBeenCalled());
    rerender({ session: RENAMED });

    // Renaming a pane must not un-maximize it: the state is filed under a name
    // this grid itself just changed, and losing it would look like the rename
    // had restarted something.
    expect(screen.getByTestId("pane-Frontend").getAttribute("data-maximized")).toBe(
      "yes",
    );
  });

  it("keeps the prompt bar pointed at the pane it was pointed at", async () => {
    vi.mocked(api.renameTerminal).mockResolvedValue(RENAMED);
    const { rerender } = renderGrid();

    expect(screen.getByLabelText(/instruction for Mika/i)).toBeTruthy();

    fireEvent.click(screen.getByTestId("pane-rename-Mika"));
    await waitFor(() => expect(api.renameTerminal).toHaveBeenCalled());
    rerender({ session: RENAMED });

    expect(screen.getByLabelText(/instruction for Frontend/i)).toBeTruthy();
  });

  it("says what went wrong and leaves the workspace alone", async () => {
    vi.mocked(api.renameTerminal).mockRejectedValue(
      new Error("Another terminal in this workspace is already called 'Nova'."),
    );
    const { onSessionChanged } = renderGrid();

    fireEvent.click(screen.getByTestId("pane-rename-Mika"));

    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith(
        "error",
        expect.stringContaining("already called"),
      ),
    );
    expect(onSessionChanged).not.toHaveBeenCalled();
  });
});

describe("chat view", () => {
  /*
   * The workspace read like a conversation: a rail of agents on the left, one
   * pane on a centred stage, the prompt bar as the composer. What these tests
   * pin is not the styling but the two contracts underneath it — switching
   * modes must not remount a pane (a remount kills the coding agent), and
   * every action the grid offers keeps working from the other mode.
   */
  const FOUR = sessionWith([
    ["Mika", 0],
    ["Nova", 1],
    ["Aria", 2],
    ["Kai", 3],
  ]);

  const cellClass = (name: string) =>
    screen.getByTestId(`pane-cell-${name}`).className;

  /*
   * The reading modes are three separate buttons, not one cycling control:
   * past two states "click again" stops telling you where you land, and the
   * third mode changes the assistant's behaviour rather than only the layout.
   */
  const toChat = () => fireEvent.click(screen.getByTestId("agentic-view-mode-toggle"));
  const toGrid = () => fireEvent.click(screen.getByTestId("agentic-view-mode-grid"));

  const railOrder = () =>
    screen
      .getAllByTestId(/^chat-rail-item-/)
      .map((item) => item.dataset.terminal);

  function placeRailItem(name: string, top: number) {
    const item = screen.getByTestId(`chat-rail-item-${name}`);
    item.getBoundingClientRect = () =>
      ({
        left: 0,
        top,
        width: 200,
        height: 36,
        right: 200,
        bottom: top + 36,
        x: 0,
        y: top,
        toJSON: () => ({}),
      }) as DOMRect;
  }

  it("starts in the grid, with the rail hidden", () => {
    renderGrid(FOUR);
    expect(screen.getByTestId("agentic-chat-rail").className).toContain("hidden");
    for (const name of ["Mika", "Nova", "Aria", "Kai"]) {
      expect(cellClass(name)).not.toContain("hidden");
    }
  });

  it("shows one pane on the stage and the rest in the rail", () => {
    renderGrid(FOUR);
    toChat();
    expect(screen.getByTestId("agentic-chat-rail").className).not.toContain("hidden");
    // Mika is the prompt target, so it takes the stage; the others hide.
    expect(cellClass("Mika")).not.toContain("hidden");
    for (const name of ["Nova", "Aria", "Kai"]) {
      expect(cellClass(name)).toContain("hidden");
      // Hidden, never unmounted — the agent behind the pane lives on.
      expect(screen.getByTestId(`pane-${name}`)).toBeTruthy();
      expect(screen.getByTestId(`chat-rail-${name}`)).toBeTruthy();
    }
  });

  it("uses the live task recap as the rail title, behind the agent's mark", async () => {
    const mixed = sessionWith([
      ["Mika", 0],
      ["Nova", 1],
    ]);
    mixed.terminals[1] = {
      ...mixed.terminals[1],
      agent: "codex",
      display_name: "Codex",
    };
    vi.mocked(api.fetchTerminalRecaps).mockResolvedValue({
      workspace_id: "ide_test",
      terminals: [
        {
          key: "nova",
          name: "Nova",
          status: "live",
          recap: "Fix provider selection priority",
          recap_detail: "Correct the fallback order used by the provider picker.",
        },
      ],
    });

    renderGrid(mixed);
    toChat();

    const title = await screen.findByTestId("chat-rail-title-Nova");
    expect(title.textContent).toBe("Fix provider selection priority");
    // The mark LEADS the row and the title follows it, the way a list of
    // conversations reads: the title is the subject, the mark says which CLI
    // is having it. The title sits inside its tooltip anchor, so the mark is
    // the ANCHOR's previous sibling.
    const mark = title.parentElement?.previousElementSibling;
    expect(mark?.getAttribute("data-testid")).toBe("agent-mark-codex");
    // `data-logo` rather than the <img>: a single-colour mark is drawn as a
    // CSS mask so it follows the theme's ink, and jsdom does not model masks.
    expect(mark?.getAttribute("data-logo")).toBe("/provider-logos/openai.svg");
  });

  it("uses the last prompt as the title while a recap is not available", () => {
    const session = sessionWith([["Mika", 0]]);
    session.terminals[0].last_prompt = "Analyze transcription omissions";

    renderGrid(session);
    toChat();

    expect(screen.getByTestId("chat-rail-title-Mika").textContent).toBe(
      "Analyze transcription omissions",
    );
  });

  it("keeps the very same pane elements across every mode and back", () => {
    // The iron rule of this component, and the one a new reading mode is most
    // likely to break: element identity is the mounting guarantee. A remounted
    // pane is a NEW element, and a new element means the WebSocket died with
    // the old one — which kills the coding agent behind it.
    renderGrid(FOUR);
    const before = screen.getByTestId("pane-Nova");

    toChat();
    expect(screen.getByTestId("pane-Nova")).toBe(before);
    toGrid();

    expect(screen.getByTestId("pane-Nova")).toBe(before);
    expect(screen.getByTestId("agentic-chat-rail").className).toContain("hidden");
    expect(cellClass("Kai")).not.toContain("hidden");
  });

  it("a rail click puts that pane on the stage", () => {
    renderGrid(FOUR);
    toChat();
    fireEvent.click(screen.getByTestId("chat-rail-Aria"));
    expect(cellClass("Aria")).not.toContain("hidden");
    expect(cellClass("Mika")).toContain("hidden");
  });

  it("activates an externally added pane on its first chat-stage render", () => {
    const { rerender } = renderGrid(FOUR);
    toChat();
    paneActiveHistory.clear();

    rerender({
      session: sessionWith([
        ["Mika", 0],
        ["Nova", 1],
        ["Aria", 2],
        ["Kai", 3],
        ["Fresh", 4],
      ]),
    });

    // Connecting once while inactive gives the PTY the narrow hidden-grid
    // geometry. The pane must own the full stage before its first effect runs.
    expect(paneActiveHistory.get("Fresh")?.[0]).toBe(true);
    expect(screen.getByTestId("pane-Fresh").getAttribute("data-active")).toBe(
      "yes",
    );
  });

  it("swaps two rail positions by dragging without moving or remounting the panes", async () => {
    renderGrid(FOUR);
    const paneBefore = screen.getByTestId("pane-Mika");
    toChat();
    placeRailItem("Mika", 0);
    placeRailItem("Nova", 36);
    placeRailItem("Aria", 72);
    placeRailItem("Kai", 108);

    fireEvent(
      screen.getByTestId("chat-rail-Mika"),
      new MouseEvent("pointerdown", {
        bubbles: true,
        clientX: 40,
        clientY: 18,
        button: 0,
      }),
    );
    act(() => {
      window.dispatchEvent(
        new MouseEvent("pointermove", { clientX: 40, clientY: 90 }),
      );
    });

    expect(screen.getByTestId("chat-rail-arrange-ghost").textContent).toContain(
      "Mika",
    );
    expect(screen.getByTestId("chat-rail-item-Aria").className).toContain(
      "ring-primary/70",
    );

    await act(async () => {
      window.dispatchEvent(new MouseEvent("pointerup"));
    });

    expect(railOrder()).toEqual(["Aria", "Nova", "Mika", "Kai"]);
    expect(api.moveTerminal).not.toHaveBeenCalled();
    expect(screen.getByTestId("pane-Mika")).toBe(paneBefore);
    // Chromium follows a drag release with a compatibility click on the row
    // below it. That click belongs to the drag and must not switch the stage.
    fireEvent.click(screen.getByTestId("chat-rail-Aria"));
    expect(cellClass("Mika")).not.toContain("hidden");
    expect(cellClass("Aria")).toContain("hidden");
    await waitFor(() =>
      expect(
        JSON.parse(
          window.localStorage.getItem("jarvis.agenticIde.chatOrder.v1.ide_test") ??
            "[]",
        ),
      ).toEqual(["aria@0", "nova@0", "mika@0", "kai@0"]),
    );

    cleanup();
    renderGrid(FOUR);
    expect(railOrder()).toEqual(["Aria", "Nova", "Mika", "Kai"]);
  });

  it("grounds deictic voice references in the pane visibly on stage", async () => {
    renderGrid(FOUR);
    await waitFor(() =>
      expect(api.syncAgenticIdeSurface).toHaveBeenLastCalledWith({
        workspaceId: "ide_test",
        view: "grid",
        onScreen: true,
        terminal: null,
        promptTarget: "Mika",
      }),
    );

    toChat();
    fireEvent.click(screen.getByTestId("chat-rail-Aria"));

    await waitFor(() =>
      expect(api.syncAgenticIdeSurface).toHaveBeenLastCalledWith({
        workspaceId: "ide_test",
        view: "chat",
        onScreen: true,
        terminal: "Aria",
        promptTarget: "Aria",
      }),
    );

    toGrid();
    await waitFor(() =>
      expect(api.syncAgenticIdeSurface).toHaveBeenLastCalledWith({
        workspaceId: "ide_test",
        view: "grid",
        onScreen: true,
        terminal: null,
        promptTarget: "Aria",
      }),
    );
  });

  /*
   * The view travels to the backend by NAME rather than as a boolean, so that
   * a third mode added later reads correctly everywhere without re-deriving
   * what "not chat" was supposed to mean. Pinned separately from the pane
   * above because it is a separate claim.
   */
  it("tells the backend which mode is on screen", async () => {
    renderGrid(FOUR);

    toChat();

    await waitFor(() =>
      expect(api.syncAgenticIdeSurface).toHaveBeenLastCalledWith(
        expect.objectContaining({ view: "chat", onScreen: true }),
      ),
    );
  });

  it("reports the grid while the section is off screen, whatever it shows", async () => {
    // A workspace nobody is looking at must not answer "this terminal". This
    // grid stays mounted behind another section, so "on screen" is the only
    // thing that can stop it — see the matching backend test.
    const { rerender } = renderGrid(FOUR);
    toChat();
    await waitFor(() =>
      expect(api.syncAgenticIdeSurface).toHaveBeenLastCalledWith(
        expect.objectContaining({ view: "chat" }),
      ),
    );

    rerender({ onScreen: false });

    await waitFor(() =>
      expect(api.syncAgenticIdeSurface).toHaveBeenLastCalledWith(
        expect.objectContaining({ onScreen: false, terminal: null }),
      ),
    );
  });

  it("remembers the chosen view for the next workspace", () => {
    renderGrid(FOUR);
    toChat();
    cleanup();
    renderGrid(FOUR);
    expect(screen.getByTestId("agentic-chat-rail").className).not.toContain("hidden");
  });

  it("maximize on the stage hands over to the grid, maximized", () => {
    renderGrid(FOUR);
    toChat();
    fireEvent.click(screen.getByTestId("pane-maximize-Mika"));
    expect(screen.getByTestId("agentic-chat-rail").className).toContain("hidden");
    expect(screen.getByTestId("pane-Mika").getAttribute("data-maximized")).toBe("yes");
  });

  it("the rail's plus opens a terminal at the end of the row", async () => {
    renderGrid(FOUR);
    toChat();
    fireEvent.click(screen.getByTestId("chat-rail-new-terminal"));
    await waitFor(() =>
      expect(api.addTerminal).toHaveBeenCalledWith({
        anchor: undefined,
        direction: "right",
        agent: undefined,
      }),
    );
  });

  /*
   * The rail's plus offers the same choice the grid's split buttons do.
   *
   * It used to open whatever CLI the backend listed first, so a workspace read
   * in chat view could only ever grow more panes of that one agent — the one
   * thing the grid had been asking about since the split menus landed.
   */
  const CLI_CHOICES = [
    { name: "claude", displayName: "Claude Code", installed: true },
    { name: "codex", displayName: "Codex", installed: true },
    {
      name: "shell",
      displayName: "Plain Terminal",
      installed: false,
      kind: "shell",
    },
  ];

  it("the rail's plus asks which CLI when more than one is installed", async () => {
    renderGrid(FOUR, { agents: CLI_CHOICES });
    toChat();
    fireEvent.click(screen.getByTestId("chat-rail-new-terminal"));

    // Uninstalled entries stay listed but disabled, so the absence is visible.
    expect(
      (screen.getByTestId("chat-rail-new-shell") as HTMLButtonElement).disabled,
    ).toBe(true);

    fireEvent.click(screen.getByTestId("chat-rail-new-codex"));
    await waitFor(() =>
      expect(api.addTerminal).toHaveBeenCalledWith({
        anchor: undefined,
        direction: "right",
        agent: "codex",
      }),
    );
    // The menu closes with the pick — it was a question, and it was answered.
    expect(screen.queryByTestId("chat-rail-agent-menu")).toBeNull();
  });

  it("the rail's plus just opens one when a single CLI is installed", async () => {
    // A menu with one entry is a click tax, not a choice.
    renderGrid(FOUR, {
      agents: [CLI_CHOICES[0], { ...CLI_CHOICES[1], installed: false }],
    });
    toChat();
    fireEvent.click(screen.getByTestId("chat-rail-new-terminal"));
    expect(screen.queryByTestId("chat-rail-agent-menu")).toBeNull();
    await waitFor(() =>
      expect(api.addTerminal).toHaveBeenCalledWith({
        anchor: undefined,
        direction: "right",
        agent: undefined,
      }),
    );
  });

  it("leaving chat view closes an open picker", () => {
    renderGrid(FOUR, { agents: CLI_CHOICES });
    toChat();
    fireEvent.click(screen.getByTestId("chat-rail-new-terminal"));
    expect(screen.getByTestId("chat-rail-agent-menu")).toBeTruthy();
    toChat();
    // The button that opened it just left the screen; it must not be waiting
    // there on the way back.
    expect(screen.queryByTestId("chat-rail-agent-menu")).toBeNull();
  });

  /*
   * Closing ONE terminal from the rail.
   *
   * Chat view shows a single pane and hides the other eleven — and with them
   * the pane header that used to be the only per-terminal close. So the rail
   * carries its own, and it goes through the same confirmation as the header:
   * closing a pane kills whatever its agent was doing, and there is no undo.
   */
  it("closes one terminal from its row in the rail", async () => {
    const survivors = sessionWith([
      ["Mika", 0],
      ["Nova", 1],
      ["Kai", 3],
    ]);
    vi.mocked(api.closeTerminal).mockResolvedValue(survivors);
    const { onSessionChanged } = renderGrid(FOUR);
    toChat();

    fireEvent.click(screen.getByTestId("chat-rail-close-Aria"));
    // Asked, never done on the spot — closing a pane kills a working agent.
    expect(api.closeTerminal).not.toHaveBeenCalled();
    expect(screen.getByText(/Close Aria\?/)).toBeTruthy();

    fireEvent.click(screen.getByTestId("confirm-close-terminal-confirm"));
    await waitFor(() => expect(api.closeTerminal).toHaveBeenCalledWith("Aria"));
    // The grid does not own the workspace; it reports the new one upwards.
    await waitFor(() => expect(onSessionChanged).toHaveBeenCalledWith(survivors));
    await waitFor(() =>
      expect(screen.queryByTestId("confirm-close-terminal")).toBeNull(),
    );
  });

  it("keeps the rail's close out of selection mode", () => {
    // That mode is here to close SEVERAL panes; a per-row close beside a
    // checkbox is two answers to one question.
    renderGrid(FOUR);
    toChat();
    expect(screen.getByTestId("chat-rail-close-Nova")).toBeTruthy();

    fireEvent.click(screen.getByTestId("terminal-selection-toggle"));

    expect(screen.queryByTestId("chat-rail-close-Nova")).toBeNull();
  });

  it("an emptied workspace asks the same question", async () => {
    // The message shown when every pane is closed opens a terminal too, and it
    // is the only way back — so it offers the same list rather than guessing.
    renderGrid(sessionWith([]), { agents: CLI_CHOICES });
    fireEvent.click(screen.getByTestId("empty-workspace-new-terminal"));
    fireEvent.click(screen.getByTestId("empty-workspace-new-codex"));
    await waitFor(() =>
      expect(api.addTerminal).toHaveBeenCalledWith({
        anchor: undefined,
        direction: "right",
        agent: "codex",
      }),
    );
  });
});

describe("terminal text size", () => {
  const FONT_KEY = "jarvis.agenticIde.terminalFontSize";

  beforeEach(() => {
    // Restated per test: `clearAllMocks` empties the call list but keeps the
    // implementation, so a size one test stores would still be answered to the
    // next one.
    vi.mocked(api.fetchTerminalUiPreferences).mockResolvedValue({
      terminal_font_size: 13,
      stored: false,
      min: 10,
      max: 20,
      default: 13,
    });
  });

  afterEach(() => {
    window.localStorage.removeItem(FONT_KEY);
  });

  it("keeps the remembered size visible in the toolbar", async () => {
    // The reason this comes from the backend and not from localStorage: the
    // desktop window is a WebView that starts every run with empty browser
    // storage, so a size kept only in the page is gone after each restart.
    vi.mocked(api.fetchTerminalUiPreferences).mockResolvedValue({
      terminal_font_size: 17,
      stored: true,
      min: 10,
      max: 20,
      default: 13,
    });
    renderGrid();

    await waitFor(() =>
      expect(screen.getByTestId("pane-Mika").getAttribute("data-font-size")).toBe("17"),
    );
    expect(screen.getByTestId("agentic-font-size-value").textContent).toBe("17");
    expect(screen.queryByTestId("agentic-view-menu-panel")).toBeNull();
  });

  it("hands a newly chosen size to the backend", async () => {
    renderGrid();
    await waitFor(() => expect(api.fetchTerminalUiPreferences).toHaveBeenCalled());

    fireEvent.click(screen.getByLabelText("Larger terminal text"));

    await waitFor(() => expect(api.saveTerminalFontSize).toHaveBeenCalledWith(14));
    expect(screen.getByTestId("pane-Mika").getAttribute("data-font-size")).toBe("14");
  });

  it("adopts a size chosen before the backend remembered them", async () => {
    // Upgrade path. Somebody who set 16 in an older build has that number in
    // this window's storage and nowhere else; the first read must hand it over
    // rather than let the default quietly replace it.
    window.localStorage.setItem(FONT_KEY, "16");
    renderGrid();

    await waitFor(() => expect(api.saveTerminalFontSize).toHaveBeenCalledWith(16));
    expect(screen.getByTestId("pane-Mika").getAttribute("data-font-size")).toBe("16");
  });

  it("keeps working when the size cannot be read", async () => {
    // An older backend, or a request that failed. The panes still open and the
    // buttons still resize them — only the memory is missing.
    vi.mocked(api.fetchTerminalUiPreferences).mockRejectedValue(new Error("404"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    renderGrid();

    await waitFor(() => expect(warn).toHaveBeenCalled());
    fireEvent.click(screen.getByLabelText("Smaller terminal text"));
    expect(screen.getByTestId("pane-Mika").getAttribute("data-font-size")).toBe("12");
    warn.mockRestore();
  });

  it("resizes on Ctrl+plus, Ctrl+minus and back to the default on Ctrl+0", async () => {
    // The chord is how this feature is actually reached — nobody looks up at a
    // toolbar to enlarge the text they are reading.
    renderGrid();
    await waitFor(() => expect(api.fetchTerminalUiPreferences).toHaveBeenCalled());
    const pane = () => screen.getByTestId("pane-Mika").getAttribute("data-font-size");

    fireEvent.keyDown(window, { key: "+", ctrlKey: true });
    await waitFor(() => expect(pane()).toBe("14"));
    fireEvent.keyDown(window, { key: "+", ctrlKey: true });
    expect(pane()).toBe("15");
    expect(screen.getByTestId("agentic-font-size-value").textContent).toBe("15");

    fireEvent.keyDown(window, { key: "-", ctrlKey: true });
    expect(pane()).toBe("14");

    fireEvent.keyDown(window, { key: "0", ctrlKey: true });
    expect(pane()).toBe("13");
    await waitFor(() => expect(api.saveTerminalFontSize).toHaveBeenLastCalledWith(13));
  });

  it("claims the chord so the WebView does not zoom the whole window as well", async () => {
    renderGrid();
    await waitFor(() => expect(api.fetchTerminalUiPreferences).toHaveBeenCalled());

    expect(fireEvent.keyDown(window, { key: "+", ctrlKey: true })).toBe(false);
  });

  it("ignores AltGr+plus — a German layout types the tilde with it", async () => {
    renderGrid();
    await waitFor(() => expect(api.fetchTerminalUiPreferences).toHaveBeenCalled());

    // AltGr arrives as Ctrl+Alt, and the character has to reach the agent.
    expect(fireEvent.keyDown(window, { key: "+", ctrlKey: true, altKey: true })).toBe(true);
    expect(screen.getByTestId("pane-Mika").getAttribute("data-font-size")).toBe("13");
  });

  it("leaves the chord alone while another section is on screen", async () => {
    // The Agentic IDE is hidden rather than unmounted, so a parked grid would
    // otherwise resize its panes while the user zooms something else.
    renderGrid(BASE, { onScreen: false });
    await waitFor(() => expect(api.fetchTerminalUiPreferences).toHaveBeenCalled());

    fireEvent.keyDown(window, { key: "+", ctrlKey: true });
    expect(screen.getByTestId("pane-Mika").getAttribute("data-font-size")).toBe("13");
  });

  it("stops at the supported bounds instead of stepping past them", async () => {
    vi.mocked(api.fetchTerminalUiPreferences).mockResolvedValue({
      terminal_font_size: 20,
      stored: true,
      min: 10,
      max: 20,
      default: 13,
    });
    renderGrid();
    await waitFor(() =>
      expect(screen.getByTestId("pane-Mika").getAttribute("data-font-size")).toBe("20"),
    );
    vi.mocked(api.saveTerminalFontSize).mockClear();

    fireEvent.keyDown(window, { key: "+", ctrlKey: true });
    expect(screen.getByTestId("pane-Mika").getAttribute("data-font-size")).toBe("20");
    // Nothing changed, so nothing is written — a held-down chord at the ceiling
    // must not turn into a request per repeat.
    expect(api.saveTerminalFontSize).not.toHaveBeenCalled();
  });

  it("disables the visible control at the supported maximum", async () => {
    vi.mocked(api.fetchTerminalUiPreferences).mockResolvedValue({
      terminal_font_size: 20,
      stored: true,
      min: 10,
      max: 20,
      default: 13,
    });
    renderGrid();

    const larger = await screen.findByLabelText("Larger terminal text");
    await waitFor(() => expect((larger as HTMLButtonElement).disabled).toBe(true));
    expect((screen.getByLabelText("Smaller terminal text") as HTMLButtonElement).disabled).toBe(
      false,
    );
  });
});
