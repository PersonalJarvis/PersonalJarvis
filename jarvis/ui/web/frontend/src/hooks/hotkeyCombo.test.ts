import { describe, expect, it } from "vitest";
import {
  chordToCombo,
  codeToKeyToken,
  codeToModifierToken,
  composeCombo,
  comboTokens,
  validateCombo,
} from "./useHotkey";

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
  it("maps letters, digits, F-keys and space to jarvis tokens", () => {
    expect(codeToKeyToken("KeyA")).toBe("a");
    expect(codeToKeyToken("KeyY")).toBe("y");
    expect(codeToKeyToken("Digit5")).toBe("5");
    expect(codeToKeyToken("F7")).toBe("f7");
    expect(codeToKeyToken("F12")).toBe("f12");
    expect(codeToKeyToken("F13")).toBe("f13");
    expect(codeToKeyToken("Space")).toBe("space");
  });

  it("maps the arrow keys to the global-hotkeys names", () => {
    expect(codeToKeyToken("ArrowUp")).toBe("up");
    expect(codeToKeyToken("ArrowDown")).toBe("down");
    expect(codeToKeyToken("ArrowLeft")).toBe("left");
    expect(codeToKeyToken("ArrowRight")).toBe("right");
  });

  it("maps the navigation / editing cluster", () => {
    expect(codeToKeyToken("Insert")).toBe("insert");
    expect(codeToKeyToken("Delete")).toBe("delete");
    expect(codeToKeyToken("Home")).toBe("home");
    expect(codeToKeyToken("End")).toBe("end");
    expect(codeToKeyToken("PageUp")).toBe("page_up");
    expect(codeToKeyToken("PageDown")).toBe("page_down");
    expect(codeToKeyToken("Enter")).toBe("enter");
    expect(codeToKeyToken("Tab")).toBe("tab");
    expect(codeToKeyToken("Backspace")).toBe("backspace");
  });

  it("maps the numpad to the library names the backend can register", () => {
    // The backend library's name is `numpad_3`, NOT `num_3` — emitting the
    // wrong name made the combo unregisterable and (all-or-nothing) killed
    // every hotkey. Match the library exactly.
    expect(codeToKeyToken("Numpad3")).toBe("numpad_3");
    expect(codeToKeyToken("Numpad0")).toBe("numpad_0");
    expect(codeToKeyToken("NumpadAdd")).toBe("add_key");
    expect(codeToKeyToken("NumpadSubtract")).toBe("subtract_key");
    expect(codeToKeyToken("NumpadMultiply")).toBe("multiply_key");
    expect(codeToKeyToken("NumpadDivide")).toBe("divide_key");
    expect(codeToKeyToken("NumpadDecimal")).toBe("decimal_key");
    expect(codeToKeyToken("NumpadEnter")).toBe("enter");
  });

  it("returns null for pure modifiers, Escape (reserved for cancel) and layout-ambiguous punctuation", () => {
    expect(codeToKeyToken("ControlLeft")).toBeNull();
    expect(codeToKeyToken("AltRight")).toBeNull();
    expect(codeToKeyToken("ShiftLeft")).toBeNull();
    expect(codeToKeyToken("MetaLeft")).toBeNull();
    expect(codeToKeyToken("Escape")).toBeNull();
    expect(codeToKeyToken("Period")).toBeNull();
  });
});

describe("codeToModifierToken", () => {
  it("maps the physical modifier codes to jarvis tokens", () => {
    expect(codeToModifierToken("ControlLeft")).toBe("ctrl");
    expect(codeToModifierToken("ControlRight")).toBe("ctrl");
    expect(codeToModifierToken("ShiftRight")).toBe("shift");
    expect(codeToModifierToken("AltLeft")).toBe("alt");
    expect(codeToModifierToken("AltRight")).toBe("right_alt");
    expect(codeToModifierToken("MetaLeft")).toBe("win");
  });

  it("returns null for non-modifier codes", () => {
    expect(codeToModifierToken("KeyA")).toBeNull();
    expect(codeToModifierToken("F5")).toBeNull();
  });
});

describe("composeCombo / comboTokens", () => {
  it("orders modifiers first then sorts keys, matching a physical chord", () => {
    expect(composeCombo(["f5", "ctrl"])).toBe("ctrl+f5");
    expect(composeCombo(["shift", "ctrl", "j"])).toBe("ctrl+shift+j");
    // Multi-key chord (the WASD / f5+f6 case), sorted for stability.
    expect(composeCombo(["f6", "f5"])).toBe("f5+f6");
    expect(composeCombo(["right_alt", "j"])).toBe("right_alt+j");
  });

  it("keeps a modifier-only selection visible (the click-to-assign path)", () => {
    // Clicking Ctrl on the on-screen keyboard used to compose to "" — the key
    // never lit up as selected and the modifier was silently dropped on the
    // next click. The intermediate state must round-trip; validateCombo (not
    // composeCombo) is what blocks saving a modifier-only combo.
    expect(composeCombo(["ctrl", "shift"])).toBe("ctrl+shift");
    expect(composeCombo(["ctrl"])).toBe("ctrl");
    expect(composeCombo([])).toBe("");
  });

  it("round-trips through comboTokens", () => {
    expect(composeCombo(comboTokens("ctrl+shift+f5"))).toBe("ctrl+shift+f5");
    expect([...comboTokens("f5+f6")].sort()).toEqual(["f5", "f6"]);
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

describe("validateCombo", () => {
  it("flags the empty combo", () => {
    expect(validateCombo("").status).toBe("empty");
    expect(validateCombo("   ").status).toBe("empty");
  });

  it("rejects a modifier-only combo with a reason", () => {
    expect(validateCombo("ctrl+shift")).toEqual({
      status: "error",
      reason: "only_modifiers",
    });
  });

  it("rejects a solo typing key (letters, digits, space, enter)", () => {
    for (const combo of ["j", "5", "space", "enter", "tab", "numpad_5"]) {
      expect(validateCombo(combo)).toEqual({
        status: "error",
        reason: "solo_typing_key",
      });
    }
  });

  it("accepts solo function keys (mirrors the backend rule)", () => {
    expect(validateCombo("f5").status).toBe("ok");
    expect(validateCombo("f13").status).toBe("ok");
  });

  it("accepts solo navigation keys but warns they fire while navigating", () => {
    expect(validateCombo("up")).toEqual({ status: "warning", reason: "solo_nav" });
    expect(validateCombo("home")).toEqual({ status: "warning", reason: "solo_nav" });
    expect(validateCombo("ctrl+up").status).toBe("ok");
  });

  it("rejects Windows-key combos (reserved by the OS)", () => {
    expect(validateCombo("win+j")).toEqual({
      status: "error",
      reason: "windows_reserved",
    });
  });

  it("rejects the OS-critical shortcuts Alt+F4 and Ctrl+C", () => {
    expect(validateCombo("alt+f4")).toEqual({ status: "error", reason: "alt_f4" });
    expect(validateCombo("ctrl+c")).toEqual({ status: "error", reason: "ctrl_c" });
    // A richer combo that merely contains them stays allowed.
    expect(validateCombo("ctrl+shift+c").status).toBe("ok");
  });

  it("rejects an overlap (subset/superset) with another action's combo", () => {
    const others = { hangup: "f1+f2", call: "f3+f4" };
    expect(validateCombo("f1", others)).toEqual({
      status: "error",
      reason: "collision",
      conflict: { action: "hangup", combo: "f1+f2" },
    });
    expect(validateCombo("f3+f4+f5", others)).toEqual({
      status: "error",
      reason: "collision",
      conflict: { action: "call", combo: "f3+f4" },
    });
  });

  it("allows sharing a modifier with another action (no chord overlap)", () => {
    expect(
      validateCombo("ctrl+shift+h", { ptt: "ctrl+right_alt+j" }).status,
    ).toBe("ok");
  });
});
