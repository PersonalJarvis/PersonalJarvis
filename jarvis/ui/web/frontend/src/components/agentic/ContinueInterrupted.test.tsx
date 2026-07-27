/**
 * Component tests for the "Continue interrupted" toolbar control.
 *
 * What matters here is not the layout but WHAT THE USER IS TOLD before and after
 * the click. Three things carry the feature and each one has been a real failure
 * mode somewhere in this app:
 *
 * 1. A pane whose agent is not running must be visibly un-continuable BEFORE the
 *    click, not after — otherwise the button promises work it cannot start.
 * 2. A pane the instruction was typed into without a confirmed submit is its own
 *    outcome. Reporting it as "carrying on" is the exact lie the backend's
 *    three-state answer exists to prevent.
 * 3. Nothing interrupted is a normal, quiet answer — never an error.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const pushToast = vi.fn();
vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ pushToast, assistantName: "Jarvis" }),
}));

vi.mock("@/lib/agenticIdeApi", () => ({
  fetchInterrupted: vi.fn(),
  continueInterrupted: vi.fn(),
}));

import { ContinueInterrupted } from "./ContinueInterrupted";
import {
  continueInterrupted,
  fetchInterrupted,
  type ContinueResult,
  type InterruptedOffer,
  type InterruptedPane,
} from "@/lib/agenticIdeApi";

function pane(over: Partial<InterruptedPane> = {}): InterruptedPane {
  return {
    workspace_id: "ide_1",
    workspace: "Personal Jarvis",
    folder: "/repo",
    key: "alex",
    name: "Alex",
    agent: "claude",
    display_name: "Claude Code",
    status: "live",
    continuable: true,
    blocked_reason: "",
    last_task: "rewrite the resume store",
    prompts_sent: 3,
    started_at: 1000,
    ...over,
  };
}

function offer(panes: InterruptedPane[]): InterruptedOffer {
  return {
    count: panes.length,
    continuable_count: panes.filter((p) => p.continuable).length,
    prompt: "continue",
    panes,
  };
}

function result(over: Partial<ContinueResult> = {}): ContinueResult {
  return {
    ok: true,
    continued: [],
    queued: [],
    unconfirmed: [],
    failed: [],
    remaining: 0,
    ...over,
  };
}

const asked = () => vi.mocked(fetchInterrupted);
const told = () => vi.mocked(continueInterrupted);


/** jest-dom is not set up here, so this asks the element itself. */
function disabled(testId: string): boolean {
  return (screen.getByTestId(testId) as HTMLButtonElement).disabled;
}

async function openDialog() {
  render(<ContinueInterrupted />);
  await waitFor(() => expect(asked()).toHaveBeenCalled());
  fireEvent.click(screen.getByTestId("continue-interrupted-open"));
  return screen.findByTestId("continue-interrupted");
}

describe("ContinueInterrupted", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    asked().mockResolvedValue(offer([]));
    told().mockResolvedValue(result());
  });

  afterEach(cleanup);

  it("counts the waiting panes on the button", async () => {
    asked().mockResolvedValue(offer([pane(), pane({ key: "blake", name: "Blake" })]));

    render(<ContinueInterrupted />);

    const badge = await screen.findByTestId("continue-interrupted-count");
    expect(badge.textContent).toBe("2");
  });

  it("stays quiet when nothing was interrupted", async () => {
    render(<ContinueInterrupted />);
    await waitFor(() => expect(asked()).toHaveBeenCalled());

    expect(screen.queryByTestId("continue-interrupted-count")).toBeNull();
  });

  it("re-checks when the dialog is opened", async () => {
    // The whole point of the button: "check what was interrupted" must be a
    // fresh answer, not whatever the background poll last happened to see.
    asked().mockResolvedValue(offer([pane()]));

    await openDialog();

    expect(asked()).toHaveBeenCalledTimes(2);
  });

  it("says which pane cannot be continued, and why, before the click", async () => {
    asked().mockResolvedValue(
      offer([
        pane({
          key: "blake",
          name: "Blake",
          status: "exited",
          continuable: false,
          blocked_reason: "Its agent stopped with exit code 1 — restart the pane first.",
        }),
      ]),
    );

    await openDialog();

    expect(screen.getByText(/restart the pane first/)).toBeTruthy();
    expect(disabled("continue-pane-Blake")).toBe(true);
    expect(disabled("continue-interrupted-all")).toBe(true);
  });

  it("continues one named pane from its own button", async () => {
    asked().mockResolvedValue(offer([pane(), pane({ key: "blake", name: "Blake" })]));
    told().mockResolvedValue(result({ continued: ["Blake"], remaining: 1 }));

    await openDialog();
    fireEvent.click(screen.getByTestId("continue-pane-Blake"));

    await waitFor(() => expect(told()).toHaveBeenCalledWith(["Blake"]));
  });

  it("continues every pane with no names at all", async () => {
    asked().mockResolvedValue(offer([pane()]));
    told().mockResolvedValue(result({ continued: ["Alex"] }));

    await openDialog();
    fireEvent.click(screen.getByTestId("continue-interrupted-all"));

    await waitFor(() => expect(told()).toHaveBeenCalledWith(undefined));
  });

  it("reports a pane that never submitted as a warning, not a success", async () => {
    asked().mockResolvedValue(offer([pane()]));
    told().mockResolvedValue(result({ ok: true, unconfirmed: ["Alex"], remaining: 1 }));

    await openDialog();
    fireEvent.click(screen.getByTestId("continue-interrupted-all"));

    await waitFor(() => expect(pushToast).toHaveBeenCalled());
    const [tone, message] = pushToast.mock.calls.at(-1) as [string, string];
    expect(tone).toBe("warning");
    expect(message).toContain("Alex");
  });

  it("reports a refusal as an error and names the reason", async () => {
    asked().mockResolvedValue(offer([pane()]));
    told().mockResolvedValue(
      result({ ok: false, failed: [{ name: "Alex", detail: "not running" }], remaining: 1 }),
    );

    await openDialog();
    fireEvent.click(screen.getByTestId("continue-interrupted-all"));

    await waitFor(() => expect(pushToast).toHaveBeenCalled());
    const [tone, message] = pushToast.mock.calls.at(-1) as [string, string];
    expect(tone).toBe("error");
    expect(message).toContain("not running");
  });

  it("closes once nothing is left waiting", async () => {
    asked().mockResolvedValueOnce(offer([pane()])).mockResolvedValueOnce(offer([pane()]));
    told().mockResolvedValue(result({ continued: ["Alex"] }));
    asked().mockResolvedValue(offer([]));

    await openDialog();
    fireEvent.click(screen.getByTestId("continue-interrupted-all"));

    await waitFor(() => expect(screen.queryByTestId("continue-interrupted")).toBeNull());
  });

  it("cannot be pressed twice while the first instruction is in flight", async () => {
    // A delivery takes seconds — the submit is verified against the pane's own
    // screen — and a live button in that window is a button somebody presses
    // again. Two presses used to mean the agent got "continue" twice.
    asked().mockResolvedValue(offer([pane()]));
    let release: (value: ContinueResult) => void = () => {};
    told().mockReturnValue(
      new Promise<ContinueResult>((resolve) => {
        release = resolve;
      }),
    );

    await openDialog();
    fireEvent.click(screen.getByTestId("continue-interrupted-all"));

    await waitFor(() => expect(disabled("continue-interrupted-all")).toBe(true));
    expect(disabled("continue-pane-Alex")).toBe(true);
    fireEvent.click(screen.getByTestId("continue-interrupted-all"));
    fireEvent.click(screen.getByTestId("continue-pane-Alex"));

    expect(told()).toHaveBeenCalledTimes(1);
    release(result({ continued: ["Alex"] }));
  });

  it("says a still-starting pane will carry on, rather than claiming it did", async () => {
    asked().mockResolvedValue(offer([pane({ starting: true, status: "pending" })]));
    told().mockResolvedValue(result({ queued: ["Alex"], remaining: 1 }));

    await openDialog();
    expect(screen.getByTestId("interrupted-starting-Alex")).toBeTruthy();
    fireEvent.click(screen.getByTestId("continue-interrupted-all"));

    await waitFor(() => expect(pushToast).toHaveBeenCalled());
    const [, message] = pushToast.mock.calls.at(-1) as [string, string];
    expect(message).toContain("Alex");
    expect(message).not.toContain("carrying on");
  });

  it("leaves a pane alone once its continue is already queued", async () => {
    // The backend is holding one for it. A second press would queue a second.
    asked().mockResolvedValue(offer([pane({ starting: true, queued: true })]));

    await openDialog();

    expect(disabled("continue-pane-Alex")).toBe(true);
  });

  it("keeps its last count when the check fails", async () => {
    // A backend hiccup must not put an error in front of somebody who is
    // working — the control is a convenience, never the workspace.
    asked().mockRejectedValue(new Error("backend is warming up"));

    render(<ContinueInterrupted />);
    await waitFor(() => expect(asked()).toHaveBeenCalled());

    expect(pushToast).not.toHaveBeenCalled();
    expect(screen.queryByTestId("continue-interrupted-count")).toBeNull();
  });
});
