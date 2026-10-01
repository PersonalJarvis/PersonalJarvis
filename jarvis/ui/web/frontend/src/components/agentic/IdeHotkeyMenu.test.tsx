import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { CTRL_HINT_DELAY_MS, IdeHotkeyMenu } from "./IdeHotkeyMenu";
import type { IdeHotkeyAction } from "./ideHotkeys";

const AGENTS = [{ name: "claude", label: "Claude Code" }, { name: "codex", label: "Codex" }];

function setup(enabled = true) {
  const actions: IdeHotkeyAction[] = [];
  const renames: string[] = [];
  const terminalKeys: string[] = [];
  const passed: number[] = [];
  const view = render(<>
    <textarea aria-label="terminal" onKeyDown={(event) => terminalKeys.push(event.key)} />
    <IdeHotkeyMenu enabled={enabled} agents={AGENTS} pane="T1" onAction={(action) => actions.push(action)} onRenamePane={(name) => renames.push(name)} onPassThrough={() => passed.push(1)} />
  </>);
  const terminal = screen.getByLabelText("terminal");
  terminal.focus();
  const key = (init: KeyboardEventInit & { key: string }) => act(() => { fireEvent.keyDown(document.activeElement ?? terminal, init); });
  const leader = () => key({ key: "b", code: "KeyB", ctrlKey: true });
  return { actions, renames, terminalKeys, passed, key, leader, view };
}

describe("IdeHotkeyMenu", () => {
  it("Ctrl+B twice hands one Ctrl+B to the pane", () => {
    const { leader, passed } = setup();
    leader();
    leader();
    expect(passed).toEqual([1]);
    expect(screen.queryByRole("dialog", { name: "IDE shortcuts" })).toBeNull();
  });

  it("opens on Ctrl+B without the terminal ever seeing the chord", () => {
    const { leader, terminalKeys } = setup();
    leader();
    expect(screen.getByRole("dialog", { name: "IDE shortcuts" })).toBeTruthy();
    expect(screen.getByText("Claude Code")).toBeTruthy();
    expect(terminalKeys).toEqual([]);
  });

  it("C then → asks for a Claude Code pane to the right, and swallows both keys", () => {
    const { leader, key, actions, terminalKeys } = setup();
    leader();
    key({ key: "c", code: "KeyC" });
    expect(screen.getByText("CLAUDE CODE")).toBeTruthy();
    key({ key: "ArrowRight", code: "ArrowRight" });
    expect(actions).toEqual([{ kind: "spawn", agent: "claude", direction: "right" }]);
    expect(terminalKeys).toEqual([]);
    expect(screen.queryByRole("dialog", { name: "IDE shortcuts" })).toBeNull();
  });

  it("R asks for the new pane name and hands it over on Enter", () => {
    const { leader, key, renames } = setup();
    leader();
    key({ key: "r", code: "KeyR" });
    const field = screen.getByRole("textbox", { name: /Rename T1/ });
    fireEvent.change(field, { target: { value: "API" } });
    act(() => { fireEvent.submit(field.closest("form")!); });
    expect(renames).toEqual(["API"]);
  });

  it("lets every key through while closed, and stays shut while disabled", () => {
    const { key, terminalKeys } = setup(false);
    key({ key: "b", code: "KeyB", ctrlKey: true });
    key({ key: "c", code: "KeyC" });
    expect(screen.queryByRole("dialog", { name: "IDE shortcuts" })).toBeNull();
    expect(terminalKeys).toEqual(["b", "c"]);
  });

  describe("the Ctrl hint", () => {
    afterEach(() => { vi.useRealTimers(); });

    it("shows what B does once Ctrl is held alone, and opens the menu on click", () => {
      vi.useFakeTimers();
      const { key } = setup();
      key({ key: "Control", code: "ControlLeft", ctrlKey: true });
      act(() => { vi.advanceTimersByTime(CTRL_HINT_DELAY_MS); });
      const hint = screen.getByRole("button", { name: /opens the IDE key menu/ });
      act(() => { fireEvent.mouseDown(hint); });
      expect(screen.getByRole("dialog", { name: "IDE shortcuts" })).toBeTruthy();
    });

    it("never flashes during a quick Ctrl+C, and leaves when Ctrl is let go", () => {
      vi.useFakeTimers();
      const { key } = setup();
      key({ key: "Control", code: "ControlLeft", ctrlKey: true });
      key({ key: "c", code: "KeyC", ctrlKey: true });
      act(() => { vi.advanceTimersByTime(CTRL_HINT_DELAY_MS * 2); });
      expect(screen.queryByRole("button", { name: /opens the IDE key menu/ })).toBeNull();
      key({ key: "Control", code: "ControlLeft", ctrlKey: true });
      act(() => { vi.advanceTimersByTime(CTRL_HINT_DELAY_MS); });
      expect(screen.getByRole("button", { name: /opens the IDE key menu/ })).toBeTruthy();
      act(() => { fireEvent.keyUp(window, { key: "Control", code: "ControlLeft" }); });
      expect(screen.queryByRole("button", { name: /opens the IDE key menu/ })).toBeNull();
    });
  });

  it("shows a PREFIX mode bar, and ? opens the full key list", () => {
    const { leader, key } = setup();
    leader();
    expect(screen.getByText("PREFIX")).toBeTruthy();
    key({ key: "?", code: "Minus", shiftKey: true });
    expect(screen.getByText("All keys")).toBeTruthy();
    key({ key: "a", code: "KeyA" });
    expect(screen.queryByRole("dialog", { name: "IDE shortcuts" })).toBeNull();
  });

  it("Escape closes the menu", () => {
    const { leader, key } = setup();
    leader();
    key({ key: "Escape", code: "Escape" });
    expect(screen.queryByRole("dialog", { name: "IDE shortcuts" })).toBeNull();
  });
});
