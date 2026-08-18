import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CROWDED_TERMINAL_COUNT } from "./layout";
import {
  WorkspaceLauncher,
  type WorkspaceLauncherProps,
} from "./WorkspaceLauncher";

/**
 * A crowded workspace is TOLD about, never refused and never questioned.
 *
 * The maintainer's rule for this screen (2026-08-11): how many terminals are
 * worth watching at once is the user's call, and the app has no idea how big
 * the display in front of it is — thirty side by side may be perfectly readable
 * on a video wall. So nothing here caps the count, reshapes it, or opens fewer
 * panes than were asked for.
 *
 * It used to make the user CONFIRM as well — an "Open it anyway" that blocked
 * the next step from twenty terminals, and from six on an ordinary window once
 * the measured sentence joined in. The maintainer retired the question
 * (2026-08-18): a wizard that makes people click past a warning to open the
 * workspace they just chose is nagging. The tests below pin what is left: a
 * sentence from ten terminals up (or when the panes measure narrow), no
 * button, and a next step that is never held back by it.
 */

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

// Nothing in these tests touches the folder picker, and rendering the real one
// reaches for the backend.
vi.mock("./FolderPicker", () => ({
  FolderPicker: () => <div data-testid="folder-picker" />,
}));

/*
 * The cell width the wizard measures, faked.
 *
 * jsdom has no 2D canvas, so the real `measureAdvance` answers null there and
 * every measured branch of this screen is skipped — which is itself the
 * behaviour a test below pins. The ones that exercise the measurement set this
 * to a real advance instead: 12 px is what the maintainer's text size 20
 * measured at, on the window the bug was reported from.
 */
const advance = { px: null as number | null };
vi.mock("@/lib/terminalFont", () => ({
  measureAdvance: () => advance.px,
}));

afterEach(() => {
  advance.px = null;
});

afterEach(cleanup);

function props(
  count: number,
  overrides: Partial<WorkspaceLauncherProps> = {},
): WorkspaceLauncherProps {
  const planned = Array.from({ length: count }, (_, index) => ({
    name: `T${index + 1}`,
    agent: "claude",
    account: null,
  }));
  return {
    addingNew: false,
    busy: false,
    folder: "/work/app",
    onSelectFolder: () => {},
    onSelectRecent: () => {},
    count,
    maxTerminals: 100,
    suggestedNames: planned.map((pane) => pane.name),
    workspaceWidthPx: 2560,
    onCount: () => {},
    planned,
    onPlanned: () => {},
    agents: [
      {
        name: "claude",
        display_name: "Claude Code",
        installed: true,
        version: "2.1",
        install_command: null,
      },
    ],
    accountsFor: () => [],
    terminalAvailable: true,
    nothingInstalled: false,
    onOpenClis: () => {},
    offer: null,
    onResume: () => {},
    onDismissOffer: () => {},
    view: "grid",
    onView: () => {},
    onStart: () => {},
    ...overrides,
  } as WorkspaceLauncherProps;
}

/** Walk to the layout step, where the count is chosen. */
function openLayoutStep(count: number, overrides = {}) {
  render(<WorkspaceLauncher {...props(count, overrides)} />);
  fireEvent.click(screen.getByText("workspace_launcher.wizard.continue_layout"));
}

const nextStep = () =>
  screen.getByText(
    "workspace_launcher.wizard.continue_agents",
  ) as HTMLElement & { closest: (s: string) => HTMLButtonElement | null };

describe("a crowded workspace is told about, not confirmed", () => {
  it("says nothing at all below the threshold", () => {
    // A sentence shown every time is a sentence nobody reads. Six terminals is
    // an ordinary workspace and must open without a word.
    openLayoutStep(6);
    expect(screen.queryByTestId("workspace-crowded-warning")).toBeNull();
    expect(nextStep().closest("button")?.disabled).toBe(false);
  });

  it("says so from ten terminals up, and lets the wizard carry on", () => {
    expect(CROWDED_TERMINAL_COUNT).toBe(10);
    openLayoutStep(CROWDED_TERMINAL_COUNT);
    const note = screen.getByTestId("workspace-crowded-warning");
    expect(note.textContent).toContain("workspace_launcher.crowded.warning");
    // Nothing to click and nothing to answer: the next step is open.
    expect(screen.queryByTestId("workspace-crowded-accept")).toBeNull();
    expect(nextStep().closest("button")?.disabled).toBe(false);
  });

  it("opens exactly the count that was asked for", () => {
    // Thirty terminals is a thing somebody may want. Nothing about the note
    // changes the workspace: it opens with the panes the user planned, all of
    // them, and no confirmation stands in the way.
    const onStart = vi.fn();
    const planned = Array.from({ length: 30 }, (_, i) => ({
      name: `T${i + 1}`,
      agent: "claude",
      account: undefined,
    }));
    render(
      <WorkspaceLauncher {...props(30, { onStart, planned })} />,
    );
    fireEvent.click(
      screen.getByText("workspace_launcher.wizard.continue_layout"),
    );
    expect(screen.getByTestId("workspace-crowded-warning")).toBeTruthy();
    for (const step of [
      "workspace_launcher.wizard.continue_agents",
      "workspace_launcher.wizard.choose_view",
      "workspace_launcher.wizard.review_workspace",
    ]) {
      fireEvent.click(screen.getByText(step));
    }
    fireEvent.click(screen.getByText("workspace_launcher.wizard.open_workspace"));
    expect(onStart).toHaveBeenCalledTimes(1);
  });
});

/**
 * …and a workspace the WINDOW makes narrow is told about too — in numbers.
 *
 * The fixed count is blind to both halves of the thing it guesses at. Twelve
 * terminals on a 1 740 px stage at text size 20 land at thirteen columns each
 * and opened in complete silence, because twelve is not twenty (reported
 * 2026-08-13). The same twelve on a video wall are fine and were never worth a
 * word. So the measurement says the specific sentence — how wide, how many fit
 * — and, like the count, it never blocks.
 */
describe("a workspace this window makes narrow is told about in numbers", () => {
  const REPORTED_WINDOW_PX = 1740;

  it("says how narrow the panes come out, and carries on", () => {
    advance.px = 12;
    openLayoutStep(12, { workspaceWidthPx: REPORTED_WINDOW_PX });

    const note = screen.getByTestId("workspace-crowded-warning");
    // The measured sentence, not the general one about "most displays".
    expect(note.textContent).toContain("workspace_launcher.crowded.measured");
    expect(screen.queryByTestId("workspace-crowded-accept")).toBeNull();
    expect(nextStep().closest("button")?.disabled).toBe(false);
  });

  it("says nothing when the same panes have room", () => {
    // A wall display, or simply a smaller text size. Nothing is wrong here and
    // a sentence would be noise.
    advance.px = 6;
    openLayoutStep(4, { workspaceWidthPx: REPORTED_WINDOW_PX });

    expect(screen.queryByTestId("workspace-crowded-warning")).toBeNull();
    expect(nextStep().closest("button")?.disabled).toBe(false);
  });

  it("stays quiet where nothing can be measured", () => {
    // No canvas to measure with. "We could not measure" must never render as a
    // warning, or the wizard shouts at everyone once.
    advance.px = null;
    openLayoutStep(8, { workspaceWidthPx: REPORTED_WINDOW_PX });

    expect(screen.queryByTestId("workspace-crowded-warning")).toBeNull();
  });
});
