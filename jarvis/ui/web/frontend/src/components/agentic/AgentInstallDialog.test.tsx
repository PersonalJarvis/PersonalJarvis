/**
 * The install dialog's one hard claim: "installed" is something the app went
 * and checked, not something it inferred.
 *
 * That distinction is the whole reason this component polls at all. A package
 * manager exiting 0 does not mean a binary is on this app's PATH — the
 * recurring macOS/Linux symptom is exactly that gap — so a dialog that turned
 * green on an exit code would be confidently wrong on the machines where it
 * matters most.
 */
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentInstallDialog } from "./AgentInstallDialog";

// xterm wants a canvas and a device-pixel ratio jsdom has not got. What the
// installer prints is WorkspaceTerminal's own test; this file is about what
// the dialog concludes from the probe.
vi.mock("../workspace/WorkspaceTerminal", () => ({
  WorkspaceTerminal: ({ installName }: { installName?: string }) => (
    <div data-testid={`stub-install-pty-${installName}`} />
  ),
}));

const recheckAgent = vi.fn();
vi.mock("@/lib/agenticIdeApi", () => ({
  recheckAgent: (name: string) => recheckAgent(name),
}));

function open(onClose = vi.fn()) {
  render(
    <AgentInstallDialog
      agent="deepseek-harness"
      displayName="DeepSeek Harness"
      command="npm install -g @deepseek-ai/dsh"
      onClose={onClose}
    />,
  );
  return onClose;
}

/** Run the poll interval forward and let its promise settle. */
async function tick(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  recheckAgent.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("AgentInstallDialog", () => {
  it("runs the installer in a terminal and shows the command it runs", () => {
    open();
    expect(
      screen.getByTestId("stub-install-pty-deepseek-harness"),
    ).toBeTruthy();
    // Readable BEFORE it matters: a user who would rather run it in their own
    // shell can take it from here and close this.
    expect(screen.getByText("npm install -g @deepseek-ai/dsh")).toBeTruthy();
  });

  it("stays honest while the probe keeps saying no", async () => {
    recheckAgent.mockResolvedValue({
      name: "deepseek-harness",
      display_name: "DeepSeek Harness",
      installed: false,
      version: null,
    });
    open();
    await tick(9000);
    expect(recheckAgent).toHaveBeenCalled();
    expect(
      screen.queryByTestId("agent-install-done-deepseek-harness"),
    ).toBeNull();
  });

  it("reports the install the moment a probe finds the binary", async () => {
    recheckAgent
      .mockResolvedValueOnce({
        name: "deepseek-harness",
        display_name: "DeepSeek Harness",
        installed: false,
        version: null,
      })
      .mockResolvedValue({
        name: "deepseek-harness",
        display_name: "DeepSeek Harness",
        installed: true,
        version: "0.1.1",
      });
    const onClose = open();

    await tick(4100);
    expect(
      screen.queryByTestId("agent-install-done-deepseek-harness"),
    ).toBeNull();

    await tick(4100);
    const done = screen.getByTestId("agent-install-done-deepseek-harness");
    // The version is part of the claim: it is what proves the app can start
    // the thing, not merely that a file appeared.
    expect(done.textContent).toContain("0.1.1");

    fireEvent.click(
      screen.getByTestId("agent-install-dismiss-deepseek-harness"),
    );
    expect(onClose).toHaveBeenCalledWith(true);
  });

  it("stops asking once the answer is yes", async () => {
    recheckAgent.mockResolvedValue({
      name: "deepseek-harness",
      display_name: "DeepSeek Harness",
      installed: true,
      version: "0.1.1",
    });
    open();
    await tick(4100);
    const afterFirst = recheckAgent.mock.calls.length;
    await tick(20000);
    // A dialog left open on a finished install must not keep spawning probes
    // for as long as the user looks at it.
    expect(recheckAgent.mock.calls.length).toBe(afterFirst);
  });

  it("keeps watching when a probe itself fails", async () => {
    recheckAgent
      .mockRejectedValueOnce(new Error("backend restarting"))
      .mockResolvedValue({
        name: "deepseek-harness",
        display_name: "DeepSeek Harness",
        installed: true,
        version: "0.1.1",
      });
    open();
    await tick(4100);
    // A failed probe is not an answer — treating it as "not installed" and
    // giving up would strand the user in front of a finished install.
    await tick(4100);
    expect(
      screen.getByTestId("agent-install-done-deepseek-harness"),
    ).toBeTruthy();
  });

  it("never claims an install it did not observe", () => {
    const onClose = open();
    fireEvent.click(
      screen.getByTestId("agent-install-dismiss-deepseek-harness"),
    );
    expect(onClose).toHaveBeenCalledWith(false);
  });
});
