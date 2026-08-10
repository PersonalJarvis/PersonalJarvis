import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgentPickerMenu, offersAgentChoice } from "./AgentPicker";

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
    expect(parseFloat(menu.style.top)).toBeLessThan(700);
    expect(parseFloat(menu.style.maxHeight)).toBeGreaterThan(0);
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
