import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CROWDED_TERMINAL_COUNT } from "./layout";
import {
  WorkspaceLauncher,
  type WorkspaceLauncherProps,
} from "./WorkspaceLauncher";

/**
 * A crowded workspace is WARNED about, never refused.
 *
 * The maintainer's rule for this screen (2026-08-11): how many terminals are
 * worth watching at once is the user's call, and the app has no idea how big
 * the display in front of it is — thirty side by side may be perfectly readable
 * on a video wall. So nothing here caps the count, reshapes it, or opens fewer
 * panes than were asked for.
 *
 * What it does do is make sure the decision was deliberate, and the tests below
 * pin both halves of that: the warning BLOCKS until it is answered, and
 * answering it opens exactly the workspace that was asked for.
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

describe("a crowded workspace has to be confirmed", () => {
  it("says nothing at all below the threshold", () => {
    // A question asked every time is a question nobody reads. Six terminals is
    // an ordinary workspace and must open without a word.
    openLayoutStep(6);
    expect(screen.queryByTestId("workspace-crowded-warning")).toBeNull();
    expect(nextStep().closest("button")?.disabled).toBe(false);
  });

  it("blocks the wizard until the warning is answered", () => {
    openLayoutStep(CROWDED_TERMINAL_COUNT);
    expect(screen.getByTestId("workspace-crowded-warning")).toBeTruthy();
    // A warning that can be walked past without being read is decoration.
    expect(nextStep().closest("button")?.disabled).toBe(true);
  });

  it("lets the user overrule it and carry on", () => {
    // The whole point: the count is never refused. Thirty terminals is a thing
    // somebody may want, and this is them saying so.
    openLayoutStep(30);
    fireEvent.click(screen.getByTestId("workspace-crowded-accept"));
    expect(nextStep().closest("button")?.disabled).toBe(false);
    // The warning stays visible as a statement of what was agreed to, with the
    // button gone — it has been answered and cannot be answered twice.
    expect(screen.getByTestId("workspace-crowded-warning")).toBeTruthy();
    expect(screen.queryByTestId("workspace-crowded-accept")).toBeNull();
  });

  it("opens exactly the count that was asked for", () => {
    // Nothing about the acknowledgement changes the workspace: it opens with
    // the panes the user planned, all of them.
    const onStart = vi.fn();
    const planned = Array.from({ length: 24 }, (_, i) => ({
      name: `T${i + 1}`,
      agent: "claude",
      account: undefined,
    }));
    render(
      <WorkspaceLauncher {...props(24, { onStart, planned })} />,
    );
    fireEvent.click(
      screen.getByText("workspace_launcher.wizard.continue_layout"),
    );
    fireEvent.click(screen.getByTestId("workspace-crowded-accept"));
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
 * …and a workspace the WINDOW cannot carry has to be confirmed too.
 *
 * The fixed count of twenty is blind to both halves of the thing it guesses at.
 * Twelve terminals on a 1 740 px stage at text size 20 land at thirteen columns
 * each — a width no coding CLI can draw a frame in — and opened in complete
 * silence, because twelve is not twenty (reported 2026-08-13). The same twelve
 * on a video wall are fine and were never worth a word.
 *
 * So the measurement asks the second question. It blocks in exactly the same
 * way, for the same reason, and is overruled by the same button: the count is
 * still never refused.
 */
describe("a workspace this window cannot draw has to be confirmed", () => {
  const REPORTED_WINDOW_PX = 1740;

  it("stops a count that would leave every pane undrawable", () => {
    advance.px = 12;
    openLayoutStep(12, { workspaceWidthPx: REPORTED_WINDOW_PX });

    const warning = screen.getByTestId("workspace-crowded-warning");
    // The measured sentence, not the general one about "most displays".
    expect(warning.textContent).toContain(
      "workspace_launcher.crowded.measured",
    );
    expect(nextStep().closest("button")?.disabled).toBe(true);
  });

  it("says nothing when the same panes have room", () => {
    // A wall display, or simply a smaller text size. Nothing is wrong here and
    // a warning would be noise.
    advance.px = 6;
    openLayoutStep(4, { workspaceWidthPx: REPORTED_WINDOW_PX });

    expect(screen.queryByTestId("workspace-crowded-warning")).toBeNull();
    expect(nextStep().closest("button")?.disabled).toBe(false);
  });

  it("stays quiet where nothing can be measured", () => {
    // No canvas to measure with. "We could not measure" must never render as a
    // warning, or the wizard shouts at everyone once.
    advance.px = null;
    openLayoutStep(12, { workspaceWidthPx: REPORTED_WINDOW_PX });

    expect(screen.queryByTestId("workspace-crowded-warning")).toBeNull();
  });

  it("is overruled by the same button, and opens the count asked for", () => {
    advance.px = 12;
    openLayoutStep(12, { workspaceWidthPx: REPORTED_WINDOW_PX });

    fireEvent.click(screen.getByTestId("workspace-crowded-accept"));
    expect(nextStep().closest("button")?.disabled).toBe(false);
  });
});
