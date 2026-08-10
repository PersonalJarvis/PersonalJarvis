/**
 * The Command Deck — a third way of reading the SAME panes.
 *
 * Its own file rather than another suite inside `AgenticGrid.test.tsx`: that
 * one is already three thousand lines about the grid, and the deck is the mode
 * that changes what the assistant DOES rather than only what the screen looks
 * like. What is pinned here is the part a redesign is most likely to break —
 * the terminals stay mounted and folded away, the room is what is on screen,
 * and every state a card can be in comes from the pane rather than from the
 * deck's own opinion.
 */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

class ResizeObserverPolyfill {
  constructor(_callback: ResizeObserverCallback) {}
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = ResizeObserverPolyfill;
}

const deckVoice = vi.hoisted(() => ({
  state: "idle" as string,
  transcription: "",
  assistantName: "Ben",
}));
const pushToast = vi.fn();
vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      pushToast,
      voiceState: deckVoice.state,
      transcription: deckVoice.transcription,
      assistantName: deckVoice.assistantName,
    }),
}));

vi.mock("@/lib/voiceApi", () => ({
  requestVoiceCall: vi.fn(async () => ({ armed: true })),
  requestVoiceHangup: vi.fn(async () => ({ stopped: true })),
}));

vi.mock("@/hooks/useTheme", () => ({ useThemeValue: () => "dark" }));

const EMPTY_QUEUE = {
  sleeping: false,
  in_conversation: false,
  on_air: null,
  pending: [],
  reports: [],
};

vi.mock("@/lib/agenticIdeApi", () => ({
  addTerminal: vi.fn(),
  attachToTerminal: vi.fn(),
  closeTerminal: vi.fn(),
  closeTerminals: vi.fn(),
  composePrompt: vi.fn(),
  moveTerminal: vi.fn(),
  renameTerminal: vi.fn(),
  promptTerminal: vi.fn(),
  clearTerminalRecap: vi.fn(),
  refreshTerminalRecap: vi.fn(),
  setTerminalRecap: vi.fn(),
  saveTerminalFontSize: vi.fn(async () => undefined),
  setIdeActiveAccount: vi.fn(),
  continueInterrupted: vi.fn(),
  markPaneNotificationsRead: vi.fn(async () => 0),
  clearPaneNotifications: vi.fn(async () => undefined),
  syncAgenticIdeSurface: vi.fn(async () => undefined),
  fetchTerminalRecaps: vi.fn(async () => ({
    workspace_id: "ide_test",
    terminals: [],
  })),
  fetchTerminalActivity: vi.fn(async () => ({
    workspace_id: "ide_test",
    terminals: [],
  })),
  fetchInterrupted: vi.fn(async () => ({
    count: 0,
    continuable_count: 0,
    prompt: "continue",
    panes: [],
  })),
  fetchPaneNotifications: vi.fn(async () => ({
    enabled: true,
    unread: 0,
    notifications: [],
  })),
  fetchTerminalUiPreferences: vi.fn(async () => ({
    terminal_font_size: 13,
    stored: false,
    min: 10,
    max: 20,
    default: 13,
  })),
  // The deck's own two calls.
  fetchDeckQueue: vi.fn(async () => EMPTY_QUEUE),
  ackDeckReport: vi.fn(async () => EMPTY_QUEUE),
  setDeckHold: vi.fn(async (_name: string, held: boolean) => held),
}));

/** xterm needs a real canvas; the deck only ever asks whether it is mounted. */
vi.mock("./AgenticTerminal", () => ({
  AgenticTerminal: ({ name }: { name: string }) => (
    <div data-testid={`pane-${name}`}>{name}</div>
  ),
}));

/** The orb draws to a canvas jsdom does not have. */
vi.mock("./VoiceOrb", () => ({
  VoiceOrb: ({ state }: { state: string }) => (
    <div data-testid="voice-orb" data-state={state} />
  ),
}));

import { AgenticGrid } from "./AgenticGrid";
import {
  clearTerminalPreview,
  publishTerminalPreview,
} from "./terminalPreview";
import * as api from "@/lib/agenticIdeApi";
import * as voiceApi from "@/lib/voiceApi";
import type { SessionState, TerminalState } from "@/lib/agenticIdeApi";

function pane(name: string, column: number, index: number): TerminalState {
  return {
    key: name.toLowerCase(),
    name,
    agent: "claude",
    display_name: "Claude Code",
    index,
    column,
    slot: 0,
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

function sessionWith(names: string[]): SessionState {
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
    terminals: names.map((name, i) => pane(name, i, i)),
  };
}

const FOUR = sessionWith(["Mika", "Nova", "Aria", "Kai"]);

function renderGrid(session = FOUR, extra: Record<string, unknown> = {}) {
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

const toDeck = () =>
  fireEvent.click(screen.getByTestId("agentic-view-mode-deck"));

beforeEach(() => {
  window.localStorage.clear();
  deckVoice.state = "idle";
  deckVoice.transcription = "";
  deckVoice.assistantName = "Ben";
  for (const name of ["Mika", "Nova", "Aria", "Kai", "T1", "T2"]) {
    clearTerminalPreview(name);
  }
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("the room", () => {
  it("puts the orb on the stage and folds every terminal away", () => {
    renderGrid();
    toDeck();

    expect(screen.getByTestId("deck-stage")).toBeTruthy();
    expect(screen.getByTestId("deck-orb")).toBeTruthy();
    for (const name of ["Mika", "Nova", "Aria", "Kai"]) {
      // Folded, never unmounted — the agent behind the pane lives on. This is
      // the iron rule of this whole section and the thing a new mode is most
      // likely to break.
      expect(screen.getByTestId(`pane-${name}`)).toBeTruthy();
      expect(screen.getByTestId(`pane-cell-${name}`).className).toContain(
        "hidden",
      );
      expect(screen.getByTestId(`deck-card-${name}`)).toBeTruthy();
    }
  });

  it("keeps the very same pane elements across grid, chat, deck and back", () => {
    renderGrid();
    const before = screen.getByTestId("pane-Nova");

    toDeck();
    expect(screen.getByTestId("pane-Nova")).toBe(before);
    fireEvent.click(screen.getByTestId("agentic-view-mode-toggle"));
    expect(screen.getByTestId("pane-Nova")).toBe(before);
    fireEvent.click(screen.getByTestId("agentic-view-mode-grid"));

    expect(screen.getByTestId("pane-Nova")).toBe(before);
  });

  it("says whether it is listening rather than looking the same either way", () => {
    // A voice-first surface that is identical whether or not the mic is live
    // is the single most uncomfortable thing it could be.
    renderGrid();
    toDeck();

    expect(screen.getByTestId("deck-orb-caption").textContent).toMatch(
      /say the wake word/i,
    );
    expect(screen.getByTestId("deck-voice-action")).toBeTruthy();
  });

  it("starts voice directly from the deck instead of opening another orb", async () => {
    const openBubble = vi.fn();
    renderGrid(FOUR, { voiceOpen: false, onToggleVoice: openBubble });
    toDeck();

    fireEvent.click(screen.getByTestId("deck-orb"));

    await waitFor(() =>
      expect(voiceApi.requestVoiceCall).toHaveBeenCalledTimes(1),
    );
    expect(openBubble).not.toHaveBeenCalled();
  });

  it("shows live speech and lets the same control end the conversation", async () => {
    deckVoice.state = "listening";
    deckVoice.transcription = "hand the test failure to T2";
    renderGrid();
    toDeck();

    expect(screen.getByTestId("deck-orb-caption").textContent).toContain(
      "hand the test failure to T2",
    );
    fireEvent.click(screen.getByTestId("deck-voice-action"));

    await waitFor(() =>
      expect(voiceApi.requestVoiceHangup).toHaveBeenCalledTimes(1),
    );
  });

  it("has no seams to drag — there is no layout to size here", () => {
    renderGrid();
    toDeck();

    expect(screen.queryAllByTestId(/^pane-seam-/)).toHaveLength(0);
  });

  it("leaves a plain terminal off the table", () => {
    // A shell is not a colleague you hand work to — it would RUN the sentence.
    const session = sessionWith(["Mika", "Sh"]);
    session.terminals[1] = {
      ...session.terminals[1],
      agent: "shell",
      display_name: "Plain Terminal",
      accepts_prompts: false,
    };
    renderGrid(session);
    toDeck();

    expect(screen.getByTestId("deck-card-Mika")).toBeTruthy();
    expect(screen.queryByTestId("deck-card-Sh")).toBeNull();
  });

  it("offers to open a terminal when the workspace is empty", () => {
    renderGrid(sessionWith([]));
    toDeck();

    expect(screen.getByTestId("deck-empty")).toBeTruthy();
    expect(screen.getByTestId("deck-open-terminal")).toBeTruthy();
    // ...and the grid's own version of that offer stays out of the way, or the
    // same question would be asked twice on one screen.
    expect(screen.queryByTestId("empty-workspace-new-terminal")).toBeNull();
  });
});

describe("unfolding a terminal", () => {
  it("shows one on demand, and gives it a way back", () => {
    renderGrid();
    toDeck();

    fireEvent.click(screen.getByTestId("deck-card-expand-Nova"));

    expect(screen.getByTestId("pane-cell-Nova").className).not.toContain(
      "hidden",
    );
    expect(screen.getByTestId("deck-fold-away")).toBeTruthy();
    // One at a time: two unfolded terminals is the grid, and the grid is one
    // button away.
    expect(screen.getByTestId("pane-cell-Mika").className).toContain("hidden");
  });

  it("folds it away again without touching the pane", () => {
    renderGrid();
    toDeck();
    const before = screen.getByTestId("pane-Nova");
    fireEvent.click(screen.getByTestId("deck-card-expand-Nova"));

    fireEvent.click(screen.getByTestId("deck-fold-away"));

    expect(screen.getByTestId("deck-stage")).toBeTruthy();
    expect(screen.getByTestId("pane-Nova")).toBe(before);
  });

  it("leaving the deck folds the open terminal away", () => {
    // Otherwise coming back would land on a workspace with one pane already
    // open, which is the deck quietly not being the deck.
    renderGrid();
    toDeck();
    fireEvent.click(screen.getByTestId("deck-card-expand-Nova"));

    fireEvent.click(screen.getByTestId("agentic-view-mode-grid"));
    toDeck();

    expect(screen.getByTestId("deck-stage")).toBeTruthy();
    expect(screen.queryByTestId("deck-fold-away")).toBeNull();
  });

  it("tells the backend which pane is on the stage", async () => {
    // "This terminal" has to resolve to what the user is actually looking at,
    // and in the deck that is the unfolded card.
    renderGrid();
    toDeck();

    fireEvent.click(screen.getByTestId("deck-card-expand-Aria"));

    await waitFor(() =>
      expect(api.syncAgenticIdeSurface).toHaveBeenLastCalledWith(
        expect.objectContaining({ view: "deck", terminal: "Aria" }),
      ),
    );
  });
});

describe("the cards", () => {
  it("shows the real coding CLI on cards that otherwise only say T1", () => {
    const session = sessionWith(["T1", "T2"]);
    session.terminals[1] = {
      ...session.terminals[1],
      agent: "codex",
      display_name: "Codex",
    };
    renderGrid(session);
    toDeck();

    expect(screen.getByTestId("deck-card-agent-label-T1").textContent).toBe(
      "Claude Code",
    );
    expect(screen.getByTestId("deck-card-agent-label-T2").textContent).toBe(
      "Codex",
    );
  });

  it("gives the terminal cards about one third more room", () => {
    renderGrid();
    toDeck();

    expect(screen.getByTestId("deck-cards").className).toContain("max-w-6xl");
    expect(screen.getByTestId("deck-card-Mika").className).toContain(
      "min-h-[15rem]",
    );
  });

  it("read their state off the pane, not out of the deck's head", async () => {
    vi.mocked(api.fetchTerminalActivity).mockResolvedValue({
      workspace_id: "ide_test",
      terminals: [
        {
          name: "Mika",
          activity: "working",
          activity_since: 1,
          worked: true,
          status: "live",
        },
        {
          name: "Nova",
          activity: "asking",
          activity_since: 1,
          worked: true,
          status: "live",
        },
        {
          name: "Aria",
          activity: "waiting",
          activity_since: 1,
          worked: false,
          status: "live",
        },
      ],
    } as never);
    renderGrid();
    toDeck();

    await waitFor(() =>
      expect(
        screen.getByTestId("deck-card-Mika").getAttribute("data-state"),
      ).toBe("working"),
    );
    expect(
      screen.getByTestId("deck-card-Nova").getAttribute("data-state"),
    ).toBe("asking");
    expect(
      screen.getByTestId("deck-card-Aria").getAttribute("data-state"),
    ).toBe("waiting");
  });

  it("shows what is actually running in the terminal instead of its recap", async () => {
    renderGrid();
    toDeck();
    act(() => {
      publishTerminalPreview("Mika", [
        "$ npm run test",
        "23 tests passed",
        "Waiting for changes...",
      ]);
    });

    await waitFor(() =>
      expect(
        screen.getByTestId("deck-card-terminal-Mika").textContent,
      ).toContain("23 tests passed"),
    );
  });

  it("take a pane over and hand it back", async () => {
    renderGrid();
    toDeck();

    fireEvent.click(screen.getByTestId("deck-card-hold-Mika"));

    await waitFor(() =>
      expect(api.setDeckHold).toHaveBeenCalledWith("Mika", true),
    );
  });

  it("show a held pane as the user's, whatever it is doing", () => {
    // The hold is about who is DRIVING rather than about the agent's state,
    // and it is the one thing on the card the user set themselves.
    const session = sessionWith(["Mika"]);
    session.terminals[0] = { ...session.terminals[0], deck_hold: true };
    renderGrid(session);
    toDeck();

    expect(
      screen.getByTestId("deck-card-Mika").getAttribute("data-state"),
    ).toBe("held");
  });

  it("report a refused hold instead of showing it as taken", async () => {
    // A client that shows a pane as held while the server disagrees is the
    // worse of the two failures: the user stops watching a pane Jarvis is
    // still assigning work to.
    vi.mocked(api.setDeckHold).mockRejectedValue(
      new Error("No terminal called 'Mika'."),
    );
    renderGrid();
    toDeck();

    fireEvent.click(screen.getByTestId("deck-card-hold-Mika"));

    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith(
        "error",
        "No terminal called 'Mika'.",
      ),
    );
  });
});

describe("the report lane", () => {
  const ONE_WAITING = {
    ...EMPTY_QUEUE,
    in_conversation: true,
    pending: [
      {
        id: "sr1",
        workspace_id: "ide_test",
        pane_key: "nova",
        pane: "Nova",
        agent: "claude",
        kind: "completed" as const,
        headline: "Finished and waiting at its prompt",
        detail: "",
        at: 0,
        state: "pending" as const,
        spoken: false,
      },
    ],
  };

  it("is on screen and says when it is empty", () => {
    renderGrid();
    toDeck();

    expect(screen.getByTestId("deck-report-lane")).toBeTruthy();
    expect(screen.getByTestId("deck-lane-empty")).toBeTruthy();
  });

  it("lists what is waiting, and the card carries the same news", async () => {
    vi.mocked(api.fetchDeckQueue).mockResolvedValue(ONE_WAITING);
    renderGrid();
    toDeck();

    await waitFor(() =>
      expect(screen.getByTestId("deck-lane-hear-Nova")).toBeTruthy(),
    );
    expect(screen.getByTestId("deck-lane-count").textContent).toBe("1");
    expect(screen.getByTestId("deck-card-dot-Nova")).toBeTruthy();
  });

  it("hears one on request", async () => {
    vi.mocked(api.fetchDeckQueue).mockResolvedValue(ONE_WAITING);
    renderGrid();
    toDeck();
    const hear = await screen.findByTestId("deck-lane-hear-Nova");

    fireEvent.click(hear);

    await waitFor(() =>
      expect(api.ackDeckReport).toHaveBeenCalledWith("sr1", "next"),
    );
  });

  it("drops one the user has already seen", async () => {
    vi.mocked(api.fetchDeckQueue).mockResolvedValue(ONE_WAITING);
    renderGrid();
    toDeck();
    const drop = await screen.findByTestId("deck-lane-drop-Nova");

    fireEvent.click(drop);

    await waitFor(() =>
      expect(api.ackDeckReport).toHaveBeenCalledWith("sr1", "drop"),
    );
  });

  it("says it has gone quiet rather than looking broken", async () => {
    // An unanswered line settles the queue on purpose — repeating yourself at
    // somebody reading code is nagging. But a surface that silently stops
    // talking is indistinguishable from one that crashed, so it says which.
    vi.mocked(api.fetchDeckQueue).mockResolvedValue({
      ...ONE_WAITING,
      sleeping: true,
    });
    renderGrid();
    toDeck();

    const wake = await screen.findByTestId("deck-lane-wake");
    fireEvent.click(wake);

    await waitFor(() =>
      expect(api.ackDeckReport).toHaveBeenCalledWith("sr1", "next"),
    );
  });

  it("asks for nothing at all while the deck is not the view", () => {
    renderGrid();

    expect(api.fetchDeckQueue).not.toHaveBeenCalled();
  });
});
