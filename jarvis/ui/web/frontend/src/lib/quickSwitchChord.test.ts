/**
 * The quick switcher chord: the matcher, the per-platform default, and the
 * combos refused because the OS or the app would never let them through.
 */
import { describe, expect, it } from "vitest";
import { codeToKeyToken } from "@/hooks/useHotkey";
import {
  chordKeyToken,
  defaultQuickSwitchCombo,
  eventMatchesChord,
  quickSwitchComboProblem,
} from "./quickSwitchChord";

const press = (code: string, mods: Partial<Record<"ctrlKey" | "altKey" | "shiftKey" | "metaKey", boolean>> = {}) => ({
  code,
  ctrlKey: false,
  altKey: false,
  shiftKey: false,
  metaKey: false,
  ...mods,
});

describe("chordKeyToken agrees with the recorder", () => {
  // The startup chunk cannot import the recorder, so its key names are restated
  // here; this pins them to the recorder's so a recorded chord always matches.
  const codes = [
    "KeyA", "KeyK", "KeyZ", "Digit0", "Digit7", "F1", "F6", "F12", "F24",
    "Space", "ArrowUp", "ArrowLeft", "Home", "End", "PageUp", "PageDown",
    "Insert", "Delete", "Enter", "NumpadEnter", "Tab", "Backspace", "Numpad3",
  ];
  it.each(codes)("%s", (code) => {
    expect(chordKeyToken(code)).toBe(codeToKeyToken(code));
  });
});

describe("eventMatchesChord", () => {
  it("matches the exact chord and nothing wider", () => {
    expect(eventMatchesChord(press("Space", { ctrlKey: true }), "ctrl+space")).toBe(true);
    expect(eventMatchesChord(press("Space", { ctrlKey: true, shiftKey: true }), "ctrl+space")).toBe(false);
    expect(eventMatchesChord(press("Space"), "ctrl+space")).toBe(false);
    expect(eventMatchesChord(press("KeyK", { ctrlKey: true }), "ctrl+space")).toBe(false);
  });

  it("reads every spelling of a modifier the recorder may store", () => {
    expect(eventMatchesChord(press("KeyP", { metaKey: true, shiftKey: true }), "cmd+shift+p")).toBe(true);
    expect(eventMatchesChord(press("KeyP", { metaKey: true, shiftKey: true }), "shift+win+p")).toBe(true);
    expect(eventMatchesChord(press("Space", { altKey: true }), "right_alt+space")).toBe(true);
  });

  it("treats AltGr's phantom Ctrl as Alt, like the recorder", () => {
    const altGr = { ...press("Space", { ctrlKey: true, altKey: true }), getModifierState: (k: string) => k === "AltGraph" };
    expect(eventMatchesChord(altGr, "alt+space")).toBe(true);
    expect(eventMatchesChord(altGr, "ctrl+alt+space")).toBe(false);
  });

  it("never fires for an empty or multi-key chord", () => {
    expect(eventMatchesChord(press("Space", { ctrlKey: true }), "")).toBe(false);
    expect(eventMatchesChord(press("F3"), "f3+f4")).toBe(false);
  });
});

describe("defaults", () => {
  it("avoids the keys macOS keeps for itself", () => {
    expect(defaultQuickSwitchCombo("pc")).toBe("ctrl+space");
    expect(defaultQuickSwitchCombo("mac")).toBe("alt+space");
    expect(quickSwitchComboProblem(defaultQuickSwitchCombo("mac"), "mac")).toBeNull();
    expect(quickSwitchComboProblem(defaultQuickSwitchCombo("pc"), "pc")).toBeNull();
  });
});

describe("quickSwitchComboProblem", () => {
  it.each([
    ["cmd+space", "mac", "os_reserved"], // Spotlight
    ["command+space", "mac", "os_reserved"],
    ["cmd+alt+space", "mac", "os_reserved"],
    ["ctrl+space", "mac", "os_reserved"], // input source switch
    ["cmd+tab", "mac", "os_reserved"],
    ["alt+space", "pc", "os_reserved"], // window system menu
    ["win+space", "pc", "os_reserved"],
    ["alt+f4", "pc", "os_reserved"],
    ["ctrl+k", "pc", "app_reserved"], // Wiki / Docs search
    ["cmd+k", "mac", "app_reserved"],
    ["ctrl+c", "pc", "app_reserved"],
    ["cmd+v", "mac", "app_reserved"],
    ["space", "pc", "typing_key"],
    ["shift+j", "pc", "typing_key"],
    ["ctrl", "pc", "one_key"],
    ["ctrl+f3+f4", "pc", "one_key"],
    ["ctrl+mouse_middle", "pc", "one_key"],
  ] as const)("refuses %s on %s (%s)", (combo, platform, problem) => {
    expect(quickSwitchComboProblem(combo, platform)).toBe(problem);
  });

  it.each([
    ["ctrl+space", "pc"],
    ["ctrl+shift+space", "pc"],
    ["ctrl+shift+k", "pc"],
    ["f6", "pc"],
    ["alt+space", "mac"],
    ["cmd+shift+space", "mac"],
    ["cmd+shift+k", "mac"],
    ["ctrl+alt+k", "mac"],
  ] as const)("accepts %s on %s", (combo, platform) => {
    expect(quickSwitchComboProblem(combo, platform)).toBeNull();
  });
});
