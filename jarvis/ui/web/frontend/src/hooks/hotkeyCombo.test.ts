import { describe, expect, it } from "vitest";
import { chordToCombo, codeToKeyToken } from "./useHotkey";

/** A minimal KeyboardEvent-shaped object for the pure combo functions. */
function ev(
  code: string,
  mods: Partial<{
    ctrlKey: boolean;
    altKey: boolean;
    shiftKey: boolean;
    metaKey: boolean;
    altGraph: boolean;
  }> = {},
) {
  return {
    code,
    ctrlKey: !!mods.ctrlKey,
    altKey: !!mods.altKey,
    shiftKey: !!mods.shiftKey,
    metaKey: !!mods.metaKey,
    getModifierState: (k: string) =>
      k === "AltGraph" ? !!mods.altGraph : false,
  };
}

describe("codeToKeyToken", () => {
  it("maps letters, digits, F-keys, space and numpad to jarvis tokens", () => {
    expect(codeToKeyToken("KeyA")).toBe("a");
    expect(codeToKeyToken("KeyY")).toBe("y");
    expect(codeToKeyToken("Digit5")).toBe("5");
    expect(codeToKeyToken("F7")).toBe("f7");
    expect(codeToKeyToken("F12")).toBe("f12");
    expect(codeToKeyToken("Space")).toBe("space");
    expect(codeToKeyToken("Numpad3")).toBe("num_3");
  });

  it("returns null for pure modifiers and unsupported keys", () => {
    expect(codeToKeyToken("ControlLeft")).toBeNull();
    expect(codeToKeyToken("AltRight")).toBeNull();
    expect(codeToKeyToken("ShiftLeft")).toBeNull();
    expect(codeToKeyToken("MetaLeft")).toBeNull();
    expect(codeToKeyToken("Period")).toBeNull();
    expect(codeToKeyToken("ArrowUp")).toBeNull();
  });
});

describe("chordToCombo", () => {
  it("builds a two-letter chord (the I+Y case)", () => {
    expect(chordToCombo(ev("KeyY"), ["i", "y"])).toBe("i+y");
  });

  it("builds a two-F-key chord (the F7+F8 case)", () => {
    expect(chordToCombo(ev("F8"), ["f7", "f8"])).toBe("f7+f8");
  });

  it("sorts non-modifier keys so order of pressing does not matter", () => {
    // f4 pressed before f3 must still normalise to the f3+f4 default form.
    expect(chordToCombo(ev("F3"), ["f4", "f3"])).toBe("f3+f4");
  });

  it("emits modifiers first, then the key (ctrl+j)", () => {
    expect(chordToCombo(ev("KeyJ", { ctrlKey: true }), ["j"])).toBe("ctrl+j");
  });

  it("treats AltGr as right_alt and drops the phantom ctrl Windows injects", () => {
    expect(
      chordToCombo(ev("KeyJ", { ctrlKey: true, altKey: true, altGraph: true }), [
        "j",
      ]),
    ).toBe("right_alt+j");
  });

  it("keeps shift as a modifier prefix", () => {
    expect(chordToCombo(ev("KeyA", { shiftKey: true }), ["a"])).toBe("shift+a");
  });

  it("returns null while only modifiers are held (no real key yet)", () => {
    expect(chordToCombo(ev("ControlLeft", { ctrlKey: true }), [])).toBeNull();
  });

  it("de-duplicates repeated tokens from key-repeat", () => {
    expect(chordToCombo(ev("KeyA"), ["a", "a"])).toBe("a");
  });
});
