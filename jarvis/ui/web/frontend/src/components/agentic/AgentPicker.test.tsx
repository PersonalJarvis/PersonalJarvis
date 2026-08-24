import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgentPickerMenu, offersAgentChoice } from "./AgentPicker";

/*
 * The install dialog embeds a real xterm pane, and xterm needs a canvas and a
 * device-pixel ratio jsdom does not have. Stubbed here because these tests are
 * about the MENU — whether the offer appears, for which entries, and what it
 * reports back. What the terminal itself does is WorkspaceTerminal's own test.
 */
vi.mock("../workspace/WorkspaceTerminal", () => ({
  WorkspaceTerminal: ({ installName }: { installName?: string }) => (
    <div data-testid={`stub-install-pty-${installName}`} />
  ),
}));

afterEach(cleanup);

function PickerHarness({ onPick = vi.fn() }: { onPick?: (agent: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        Open picker
      </button>
      {open && (
        <AgentPickerMenu
          title="Open what?"
          ariaLabel="Choose a terminal"
          agents={[
            { name: "codex", displayName: "Codex", installed: true },
            { name: "claude", displayName: "Claude Code", installed: false },
          ]}
          onPick={onPick}
          onDismiss={() => setOpen(false)}
          testId="picker"
          itemTestId={(agent) => `pick-${agent}`}
        />
      )}
    </div>
  );
}

describe("AgentPickerMenu", () => {
  it("renders a labelled menu and keeps unavailable choices visible but inert", () => {
    const onPick = vi.fn();
    render(<PickerHarness onPick={onPick} />);
    fireEvent.click(screen.getByRole("button", { name: "Open picker" }));

    expect(screen.getByRole("menu", { name: "Choose a terminal" })).toBeTruthy();

    // A CLI that is not installed stays listed — the absence explains itself —
    // but is a real disabled button, so clicking it picks nothing.
    const unavailable = screen.getByTestId("pick-claude");
    expect(unavailable.hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("not installed")).toBeTruthy();
    fireEvent.click(unavailable);
    expect(onPick).not.toHaveBeenCalled();
  });

  it("focuses the first installed entry and picks it on click", () => {
    const onPick = vi.fn();
    render(<PickerHarness onPick={onPick} />);
    fireEvent.click(screen.getByRole("button", { name: "Open picker" }));

    // Keyboard users land on the first actionable choice, not the wrapper.
    expect(document.activeElement).toBe(screen.getByTestId("pick-codex"));

    fireEvent.click(screen.getByTestId("pick-codex"));
    expect(onPick).toHaveBeenCalledWith("codex");
  });

  it("Escape and a click outside both dismiss the menu", () => {
    render(<PickerHarness />);
    const trigger = screen.getByRole("button", { name: "Open picker" });

    fireEvent.click(trigger);
    fireEvent.keyDown(screen.getByTestId("picker"), { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();

    // The backdrop covers everything else, so a mousedown anywhere outside the
    // menu lands on it and closes without a global listener.
    fireEvent.click(trigger);
    fireEvent.mouseDown(
      screen.getByTestId("picker").previousElementSibling as Element,
    );
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

/**
 * A terminal pane is `overflow-hidden` by necessity — xterm's canvas must not
 * paint past the frame — so a menu positioned inside one is cut off at its
 * edge. In a twelve-pane wall that left a sliver of the first entry and nothing
 * to pick from. Detached, the menu is measured against the bar it belongs to
 * and drawn in front of the window instead.
 */
describe("AgentPickerMenu anchored outside its caller", () => {
  const VIEWPORT = { width: 1024, height: 768 };

  function AnchoredHarness({ rect }: { rect: { top: number; bottom: number; right: number } }) {
    const [anchor, setAnchor] = useState<HTMLElement | null>(null);
    return (
      <div
        data-testid="anchor"
        ref={(node) => {
          if (!node || anchor) return;
          node.getBoundingClientRect = () =>
            ({
              ...rect,
              left: rect.right - 300,
              width: 300,
              height: rect.bottom - rect.top,
            }) as DOMRect;
          setAnchor(node);
        }}
      >
        {anchor && (
          <AgentPickerMenu
            title="Open what?"
            ariaLabel="Choose a terminal"
            agents={[{ name: "codex", displayName: "Codex", installed: true }]}
            onPick={vi.fn()}
            onDismiss={vi.fn()}
            testId="picker"
            itemTestId={(agent) => `pick-${agent}`}
            className="right-2 top-full mt-1"
            anchorTo={anchor}
          />
        )}
      </div>
    );
  }

  it("hangs under the anchor, right-aligned, and drops the caller's inset classes", () => {
    window.innerWidth = VIEWPORT.width;
    window.innerHeight = VIEWPORT.height;
    render(<AnchoredHarness rect={{ top: 100, bottom: 130, right: 700 }} />);

    const menu = screen.getByTestId("picker");
    expect(menu.dataset.detached).toBe("true");
    expect(menu.style.position).toBe("fixed");
    expect(parseFloat(menu.style.top)).toBeGreaterThan(130);
    // Right edge on the anchor's right edge: that is where the buttons that
    // open it sit.
    expect(parseFloat(menu.style.left) + parseFloat(menu.style.width)).toBe(700);
    // The caller's anchoring describes a box INSIDE its own element, which is
    // the very thing a detached menu is escaping.
    expect(menu.className).not.toContain("top-full");
    expect(menu.className).not.toContain("absolute");
  });

  it("flips above the anchor when a pane sits at the bottom of the screen", () => {
    window.innerWidth = VIEWPORT.width;
    window.innerHeight = VIEWPORT.height;
    render(<AnchoredHarness rect={{ top: 700, bottom: 730, right: 700 }} />);

    const menu = screen.getByTestId("picker");
    // Anchored by its BOTTOM edge, and by nothing else: a `top` computed from
    // the room above would drop a short list at the start of that room, which
    // is the window's top edge rather than the button it belongs to.
    expect(menu.style.top).toBe("");
    expect(parseFloat(menu.style.bottom)).toBe(VIEWPORT.height - 700 + 4);
    expect(parseFloat(menu.style.maxHeight)).toBeGreaterThan(0);
  });

  /*
   * The pane sat in the lower half of a tall window with 640px of clear space
   * under its header — room enough for the whole list — and the menu still
   * went up, because the old rule only compared the two gaps and "above" won
   * by 50px. Combined with the top-anchored flip, the menu landed against the
   * window's top edge, half a screen from the split button that opened it
   * (maintainer report 2026-08-24).
   */
  it("stays under the anchor when the room below holds the list, however low the pane sits", () => {
    window.innerWidth = 2560;
    window.innerHeight = 1370;
    render(<AnchoredHarness rect={{ top: 703, bottom: 717, right: 838 }} />);

    const menu = screen.getByTestId("picker");
    expect(menu.style.bottom).toBe("");
    expect(parseFloat(menu.style.top)).toBe(717 + 4);
  });

  it("keeps a menu opened near the right edge inside the window", () => {
    window.innerWidth = VIEWPORT.width;
    window.innerHeight = VIEWPORT.height;
    render(<AnchoredHarness rect={{ top: 100, bottom: 130, right: 1024 }} />);

    const menu = screen.getByTestId("picker");
    expect(
      parseFloat(menu.style.left) + parseFloat(menu.style.width),
    ).toBeLessThanOrEqual(VIEWPORT.width);
  });
});

describe("offersAgentChoice", () => {
  it("only offers a menu when more than one choice is actually installed", () => {
    expect(offersAgentChoice(undefined)).toBe(false);
    expect(
      offersAgentChoice([{ name: "codex", displayName: "Codex", installed: true }]),
    ).toBe(false);
    expect(
      offersAgentChoice([
        { name: "codex", displayName: "Codex", installed: true },
        { name: "claude", displayName: "Claude Code", installed: false },
      ]),
    ).toBe(false);
    expect(
      offersAgentChoice([
        { name: "codex", displayName: "Codex", installed: true },
        { name: "shell", displayName: "Plain Terminal", installed: true },
      ]),
    ).toBe(true);
  });
});

describe("AgentPickerMenu install offer", () => {
  function InstallHarness({
    onInstalled = vi.fn(),
  }: {
    onInstalled?: (agent: string, installed: boolean) => void;
  }) {
    return (
      <AgentPickerMenu
        title="Open what?"
        ariaLabel="Choose a terminal"
        agents={[
          { name: "codex", displayName: "Codex", installed: true },
          {
            name: "deepseek-harness",
            displayName: "DeepSeek Harness",
            installed: false,
            installCommand: "npm install -g @deepseek-ai/dsh",
          },
          // A CLI the user added: not installed, and nothing this app knows how
          // to install — its command is theirs to run.
          { name: "mycli", displayName: "My CLI", installed: false },
          // A host with no shell at all. Nothing to install here either, and
          // the reason is a different one, so the wording must not be shared.
          {
            name: "shell",
            displayName: "Plain Terminal",
            installed: false,
            kind: "shell",
          },
        ]}
        onPick={vi.fn()}
        onDismiss={vi.fn()}
        onInstalled={onInstalled}
        testId="picker"
        itemTestId={(agent) => `pick-${agent}`}
      />
    );
  }

  it("offers to install exactly the entries an install command exists for", () => {
    render(<InstallHarness />);

    // The whole point: a missing CLI is now a button, not a dead label.
    expect(screen.getByTestId("pick-deepseek-harness-install")).toBeTruthy();
    // ...and the row it belongs to still says, by being disabled, that it
    // cannot be opened yet.
    expect(
      screen.getByTestId("pick-deepseek-harness").hasAttribute("disabled"),
    ).toBe(true);

    // No install command, no button — offering one would run nothing.
    expect(screen.queryByTestId("pick-mycli-install")).toBeNull();
    expect(screen.queryByTestId("pick-shell-install")).toBeNull();
    // Those two keep the label, and each keeps its OWN reason.
    expect(screen.getByText("not installed")).toBeTruthy();
    expect(screen.getByText("no shell here")).toBeTruthy();
  });

  it("does not repeat the label beside the button it made redundant", () => {
    render(<InstallHarness />);
    // One label, and it belongs to the custom CLI — not two, which in a 272px
    // menu would cost an entry's description its line to say nothing new.
    expect(screen.getAllByText("not installed")).toHaveLength(1);
  });

  it("opens the installer for the entry whose button was pressed", () => {
    render(<InstallHarness />);
    fireEvent.click(screen.getByTestId("pick-deepseek-harness-install"));
    expect(
      screen.getByTestId("agent-install-dialog-deepseek-harness"),
    ).toBeTruthy();
  });

  it("tells the host what happened, so the row can stop saying 'not installed'", () => {
    const onInstalled = vi.fn();
    render(<InstallHarness onInstalled={onInstalled} />);
    fireEvent.click(screen.getByTestId("pick-deepseek-harness-install"));
    fireEvent.click(
      screen.getByTestId("agent-install-dismiss-deepseek-harness"),
    );
    // False here is not a failure report — nothing probed positive while the
    // dialog was open. The host re-reads either way; what it must never do is
    // believe an install happened because a dialog was closed.
    expect(onInstalled).toHaveBeenCalledWith("deepseek-harness", false);
    expect(
      screen.queryByTestId("agent-install-dialog-deepseek-harness"),
    ).toBeNull();
  });
});
