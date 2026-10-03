import { describe, expect, it } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { IdeHotkeyMenu } from "./IdeHotkeyMenu";
import type { IdeHotkeyAction } from "./ideHotkeys";

const AGENTS = [{ name: "claude", label: "Claude Code" }, { name: "codex", label: "Codex" }];

function setup(enabled = true) {
  const actions: IdeHotkeyAction[] = [];
  const renames: string[] = [];
  const terminalKeys: string[] = [];
  const passed: number[] = [];
  const view = render(<>
    <textarea aria-label="terminal" onKeyDown={(event) => terminalKeys.push(event.key)} />
    <IdeHotkeyMenu enabled={enabled} agents={AGENTS} pane="T1" onAction={(action) => actions.push(action)}
      onRenamePane={(name) => renames.push(name)} onPassThrough={() => passed.push(1)} />
  </>);
  const terminal = screen.getByLabelText("terminal");
  terminal.focus();
  const key = (init: KeyboardEventInit & { key: string }) => act(() => { fireEvent.keyDown(document.activeElement ?? terminal, init); });
  const leader = () => key({ key: "b", code: "KeyB", ctrlKey: true });
  const isOn = () => screen.queryByRole("dialog", { name: "IDE shortcuts" }) !== null;
  return { actions, renames, terminalKeys, passed, key, leader, isOn, view };
}

describe("IdeHotkeyMenu", () => {
  it("one Ctrl+B turns the mode on, without the terminal seeing the chord", () => {
    const { leader, terminalKeys, isOn } = setup();
    leader();
    expect(isOn()).toBe(true);
    expect(screen.getByText("PREFIX")).toBeTruthy();
    expect(screen.getByText("Claude Code")).toBeTruthy();
    expect(terminalKeys).toEqual([]);
  });

  it("stays on across actions until Escape", () => {
    const { leader, key, actions, isOn, terminalKeys } = setup();
    leader();
    key({ key: "ArrowLeft", code: "ArrowLeft" });
    key({ key: "ArrowDown", code: "ArrowDown" });
    key({ key: "v", code: "KeyV" });
    expect(actions).toEqual([
      { kind: "focus-pane", direction: "left" },
      { kind: "focus-pane", direction: "down" },
      { kind: "split", direction: "right" },
    ]);
    expect(isOn()).toBe(true);
    key({ key: "Escape", code: "Escape" });
    expect(isOn()).toBe(false);
    expect(terminalKeys).toEqual([]);
  });

  it("C then → asks for a Claude Code pane to the right and stays on", () => {
    const { leader, key, actions, isOn } = setup();
    leader();
    key({ key: "c", code: "KeyC" });
    expect(screen.getByText("CLAUDE CODE")).toBeTruthy();
    key({ key: "ArrowRight", code: "ArrowRight" });
    expect(actions).toEqual([{ kind: "spawn", agent: "claude", direction: "right" }]);
    expect(screen.getByText("PREFIX")).toBeTruthy();
    expect(isOn()).toBe(true);
  });

  it("swallows a key it does not use instead of typing it into the agent", () => {
    const { leader, key, terminalKeys, isOn } = setup();
    leader();
    key({ key: "j", code: "KeyJ" });
    expect(terminalKeys).toEqual([]);
    expect(isOn()).toBe(true);
  });

  it("lets a Ctrl chord through and leaves the mode", () => {
    const { leader, key, terminalKeys, isOn } = setup();
    leader();
    key({ key: "c", code: "KeyC", ctrlKey: true });
    expect(terminalKeys).toEqual(["c"]);
    expect(isOn()).toBe(false);
  });

  it("hands over the agent picker and leaves the mode for its dialog", () => {
    const { leader, key, actions, isOn } = setup();
    leader();
    key({ key: "+", code: "BracketRight" });
    expect(actions).toEqual([{ kind: "agent-picker" }]);
    expect(isOn()).toBe(false);
  });

  it("Ctrl+B again hands one Ctrl+B to the pane and leaves", () => {
    const { leader, passed, isOn } = setup();
    leader();
    leader();
    expect(passed).toEqual([1]);
    expect(isOn()).toBe(false);
  });

  it("R renames inside the bar and returns to the mode", () => {
    const { leader, key, renames } = setup();
    leader();
    key({ key: "r", code: "KeyR" });
    const field = screen.getByRole("textbox", { name: "Rename T1" });
    fireEvent.change(field, { target: { value: "API" } });
    act(() => { fireEvent.submit(field.closest("form")!); });
    expect(renames).toEqual(["API"]);
    expect(screen.getByText("PREFIX")).toBeTruthy();
  });

  it("? shows every key; the next key goes back to the bar", () => {
    const { leader, key, isOn } = setup();
    leader();
    key({ key: "?", code: "Minus", shiftKey: true });
    expect(screen.getByRole("region", { name: "All keys" })).toBeTruthy();
    key({ key: "a", code: "KeyA" });
    expect(screen.queryByRole("region", { name: "All keys" })).toBeNull();
    expect(isOn()).toBe(true);
  });

  it("lets every key through while off, and stays off while disabled", () => {
    const { key, terminalKeys, isOn } = setup(false);
    key({ key: "b", code: "KeyB", ctrlKey: true });
    key({ key: "c", code: "KeyC" });
    expect(isOn()).toBe(false);
    expect(terminalKeys).toEqual(["b", "c"]);
  });
});
