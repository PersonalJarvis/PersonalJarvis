import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgentPickerMenu } from "./AgentPicker";

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
  it("uses ordinary dialog controls and keeps unavailable choices explainable", () => {
    const onPick = vi.fn();
    render(<PickerHarness onPick={onPick} />);
    fireEvent.click(screen.getByRole("button", { name: "Open picker" }));

    expect(screen.getByRole("dialog", { name: "Choose a terminal" })).toBeTruthy();
    const unavailable = screen.getByTestId("pick-claude");
    expect(unavailable.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(unavailable);
    expect(onPick).not.toHaveBeenCalled();
  });

  it("Escape closes the dialog and restores focus to its trigger", () => {
    render(
      <StrictMode>
        <PickerHarness />
      </StrictMode>,
    );
    const trigger = screen.getByRole("button", { name: "Open picker" });
    trigger.focus();
    fireEvent.click(trigger);

    expect(document.activeElement).toBe(screen.getByTestId("pick-codex"));
    fireEvent.keyDown(document.activeElement as Element, { key: "Escape" });

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("focuses the setup dialog when no choice is installed", () => {
    render(
      <AgentPickerMenu
        title="Open what?"
        ariaLabel="Choose a terminal"
        agents={[]}
        onPick={vi.fn()}
        onDismiss={vi.fn()}
        testId="empty-picker"
        itemTestId={(agent) => `empty-${agent}`}
      />,
    );

    expect(document.activeElement).toBe(screen.getByRole("dialog"));
  });
});
