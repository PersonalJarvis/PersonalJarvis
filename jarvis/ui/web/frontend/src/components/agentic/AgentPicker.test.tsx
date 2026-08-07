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
