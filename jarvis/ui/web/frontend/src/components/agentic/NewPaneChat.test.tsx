/**
 * The new chat: an empty chat window with no agent behind it yet.
 *
 * What the store decides is pinned in store/newPaneChat.test.ts; here it is
 * the window around it — that it IS the front page's chat (the folder
 * headline, the composer with its picks), that the coding CLIs are what the
 * provider pick offers, and that opening it starts nothing until a message is
 * sent. That last one is the whole point of the surface: the picks have to be
 * choosable while nothing is running, because they go on the CLI's command
 * line when it starts.
 */
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NewPaneChat } from "@/components/agentic/NewPaneChat";
import type { SplitAgentChoice } from "@/components/agentic/AgentPicker";
import type { NewPaneRequest } from "@/store/newPaneChat";
import { useEventStore } from "@/store/events";

const CLAUDE: SplitAgentChoice = {
  name: "claude",
  displayName: "Claude Code",
  installed: true,
  kind: "cli",
  picks: {
    models: [
      { id: "claude-opus-5", label: "Claude Opus 5" },
      { id: "claude-sonnet-5", label: "Claude Sonnet 5" },
    ],
    defaultModel: "",
    effortLevels: ["low", "medium", "high"],
    defaultEffort: "high",
    permissionModes: [
      { id: "default", label: "Ask before acting", description: "Reads run freely." },
      { id: "plan", label: "Plan", description: "Read and plan only." },
    ],
    defaultPermissionMode: "",
  },
};

const CODEX: SplitAgentChoice = {
  name: "codex",
  displayName: "Codex",
  installed: true,
  kind: "cli",
  picks: {
    models: [{ id: "gpt-5.6-sol", label: "GPT-5.6 Sol" }],
    defaultModel: "",
    effortLevels: ["low", "medium", "high", "xhigh"],
    defaultEffort: "medium",
    permissionModes: [{ id: "auto", label: "Auto", description: "Edits run on their own." }],
    defaultPermissionMode: "",
  },
};

const SHELL: SplitAgentChoice = {
  name: "shell",
  displayName: "Plain Terminal",
  installed: true,
  kind: "shell",
};

function draw(overrides: Partial<React.ComponentProps<typeof NewPaneChat>> = {}) {
  // The composer refuses to type while the app's socket is down, which in a
  // test is simply "nobody connected it".
  useEventStore.setState({ connected: true, wsWarming: false });
  const onOpen = vi.fn(async (_request: NewPaneRequest) => undefined);
  const onDismiss = vi.fn();
  render(
    <NewPaneChat
      folder="C:/work/personal-jarvis"
      agents={[CLAUDE, CODEX, SHELL]}
      onOpen={onOpen}
      onDismiss={onDismiss}
      {...overrides}
    />,
  );
  return { onOpen, onDismiss };
}

afterEach(cleanup);

describe("the Agentic IDE's new chat", () => {
  it("is an empty chat window on the workspace's folder", () => {
    draw();
    expect(screen.getByTestId("new-pane-chat")).toBeTruthy();
    // The front page's own empty page, headlined by the folder the agent will
    // work in — not a dialog of its own.
    const stage = screen.getByTestId("chat-stage");
    expect(stage.dataset.empty).toBe("true");
    expect(screen.getByTestId("chat-folder-headline").textContent).toContain("personal-jarvis");
  });

  it("offers the coding CLIs, and not the plain terminal", () => {
    draw();
    fireEvent.click(screen.getByTestId("composer-provider"));
    const menu = screen.getByRole("listbox");
    expect(within(menu).getByText("Claude Code")).toBeTruthy();
    expect(within(menu).getByText("Codex")).toBeTruthy();
    // A shell has no conversation to have.
    expect(within(menu).queryByText("Plain Terminal")).toBeNull();
  });

  it("opens nothing while the picks are being made", () => {
    const { onOpen } = draw();
    fireEvent.click(screen.getByTestId("composer-provider"));
    fireEvent.click(screen.getByText("Codex"));
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("starts the pane on the picks when the first message is sent", async () => {
    const { onOpen } = draw();
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "read the router and tell me what it does" },
    });
    fireEvent.click(screen.getByTestId("composer-send"));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen.mock.calls[0][0]).toMatchObject({
      agent: "claude",
      effort: "high",
      text: "read the router and tell me what it does",
    });
  });

  it("leaves without starting anything on Escape", () => {
    const { onDismiss, onOpen } = draw();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onDismiss).toHaveBeenCalled();
    expect(onOpen).not.toHaveBeenCalled();
  });
});
