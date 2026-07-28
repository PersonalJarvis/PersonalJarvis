import { describe, expect, it } from "vitest";
import {
  chordToCombo,
  codeToKeyToken,
  codeToModifierToken,
  composeCombo,
  comboTokens,
  normalizedComboTokens,
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
    expect(codeToModifierToken("MetaLeft", "pc")).toBe("win");
  });

  it("names the Meta key the way the host's keyboard does", () => {
    // The same physical key, two vocabularies the backends genuinely tell
    // apart. Emitting "win" on a Mac produced a combo the Mac backend cannot
    // match, and made "cmd" — the token it DOES want — unreachable from the UI.
    expect(codeToModifierToken("MetaLeft", "mac")).toBe("cmd");
    expect(codeToModifierToken("MetaRight", "mac")).toBe("cmd");
    expect(codeToModifierToken("MetaRight", "pc")).toBe("win");
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

  it("records a modifier-only chord instead of dropping it", () => {
    // This used to return null the moment no non-modifier key was held, which
    // is why holding Ctrl+Win recorded literally NOTHING: the field stayed
    // blank and the gesture expired on the idle timer with no feedback at all.
    // The combo is legal (the backends register it happily), so the recorder
    // has to be able to produce it.
    expect(
      chordToCombo(ev("MetaLeft", { ctrlKey: true, metaKey: true }), [], "pc"),
    ).toBe("ctrl+win");
    expect(chordToCombo(ev("ControlLeft", { ctrlKey: true }), [])).toBe("ctrl");
    expect(
      chordToCombo(ev("MetaLeft", { ctrlKey: true, metaKey: true }), [], "mac"),
    ).toBe("ctrl+cmd");
  });

  it("returns null only when nothing at all is held", () => {
    expect(chordToCombo(ev("Escape"), [])).toBeNull();
  });

  it("keeps the Meta key in a chord with a real key", () => {
    expect(chordToCombo(ev("KeyJ", { metaKey: true }), ["j"], "pc")).toBe("win+j");
    expect(chordToCombo(ev("KeyJ", { metaKey: true }), ["j"], "mac")).toBe("cmd+j");
  });

  it("builds a mouse-button chord (the token names the backends register)", () => {
    // A mouse press carries the modifier flags but no `code`; the held-set is
    // the same one the keys use, so Ctrl + side button is one ordinary chord.
    expect(chordToCombo(ev("", { ctrlKey: true }), ["mouse_x1"])).toBe(
      "ctrl+mouse_x1",
    );
    expect(chordToCombo(ev(""), ["mouse_middle"])).toBe("mouse_middle");
  });

  it("de-duplicates repeated tokens from key-repeat", () => {
    expect(chordToCombo(ev("KeyA"), ["a", "a"])).toBe("a");
  });
});

describe("normalizedComboTokens", () => {
  it("folds the spellings the Windows backend cannot tell apart", () => {
    // ctrl+left_alt+j and ctrl+right_alt+j are ONE registration. Comparing the
    // raw tokens accepted both and the second then died at register time,
    // leaving a bound-looking row that does nothing.
    expect([...normalizedComboTokens("ctrl+left_alt+j")].sort()).toEqual(
      [...normalizedComboTokens("ctrl+right_alt+j")].sort(),
    );
    expect([...normalizedComboTokens("ctrl+super+space")].sort()).toEqual(
      [...normalizedComboTokens("ctrl+win+space")].sort(),
    );
  });
});

describe("validateCombo", () => {
  it("flags the empty combo", () => {
    expect(validateCombo("").status).toBe("empty");
    expect(validateCombo("   ").status).toBe("empty");
  });

  it("accepts a modifier-only combo and cautions that it fires on any superset", () => {
    // Inverted on purpose (maintainer directive 2026-07-28: any combination is
    // selectable). The refusal it replaces made Ctrl+Win unbindable while
    // claiming it "cannot be a trigger" — untrue: the backends register it and
    // it fires. What IS true is the prefix behaviour, so that is now said out
    // loud instead of used as a wall. A silent acceptance would be the real
    // regression, so the caution is asserted, not just the status.
    const v = validateCombo("ctrl+win");
    expect(v.status).toBe("ok");
    expect(v.cautions).toContain("modifier_only");
    expect(validateCombo("ctrl+shift").cautions).toContain("modifier_only");
  });

  it("accepts a solo typing key (letters, digits, space, enter) with a caution", () => {
    for (const combo of ["j", "5", "space", "enter", "tab", "numpad_5"]) {
      const v = validateCombo(combo);
      expect(v.status).toBe("ok");
      expect(v.cautions).toContain("solo_typing_key");
    }
  });

  it("accepts solo function keys with no caution at all (mirrors the backend rule)", () => {
    expect(validateCombo("f5")).toEqual({ status: "ok", cautions: [] });
    expect(validateCombo("f13")).toEqual({ status: "ok", cautions: [] });
  });

  it("accepts solo navigation keys but warns they fire while navigating", () => {
    expect(validateCombo("up").cautions).toEqual(["solo_nav"]);
    expect(validateCombo("home").cautions).toEqual(["solo_nav"]);
    expect(validateCombo("ctrl+up")).toEqual({ status: "ok", cautions: [] });
  });

  it("accepts Windows-key combos — the backend polls, the shell cannot claim them first", () => {
    // Inverted: the old refusal's own justification (the shell owns Win
    // shortcuts) does not hold for this app, whose Windows backend reads the
    // key state instead of registering a hotkey with the shell.
    expect(validateCombo("win+j")).toEqual({ status: "ok", cautions: [] });
    // A Win chord the desktop ALSO acts on is cautioned, not refused.
    expect(validateCombo("win+d", {}, "pc").cautions).toContain("os_shortcut");
  });

  it("accepts F12 in any combo but says the debugger takes it too", () => {
    expect(validateCombo("f12").cautions).toEqual(["os_shortcut"]);
    expect(validateCombo("ctrl+shift+f12").cautions).toEqual(["os_shortcut"]);
    // Its neighbours stay caution-free.
    expect(validateCombo("f11").cautions).toEqual([]);
    expect(validateCombo("ctrl+shift+f13").cautions).toEqual([]);
  });

  it("accepts the OS-critical shortcuts Alt+F4 and Ctrl+C with a caution", () => {
    expect(validateCombo("alt+f4").cautions).toEqual(["os_shortcut"]);
    expect(validateCombo("ctrl+c").cautions).toEqual(["os_shortcut"]);
    // A richer combo that merely contains them stays silent.
    expect(validateCombo("ctrl+shift+c").cautions).toEqual([]);
  });

  it("cautions a Command chord macOS keeps for itself", () => {
    expect(validateCombo("cmd+q").cautions).toContain("os_shortcut");
    expect(validateCombo("cmd+shift+q").cautions).toEqual([]);
  });

  it("accepts mouse buttons and says the click is not swallowed", () => {
    const v = validateCombo("mouse_x1");
    expect(v.status).toBe("ok");
    expect(v.cautions).toEqual(["mouse_button"]);
    // A bare mouse button is NOT a "fires while you type" case.
    expect(v.cautions).not.toContain("solo_typing_key");
    expect(validateCombo("ctrl+mouse_middle").cautions).toEqual(["mouse_button"]);
  });

  it("allows an overlap with another action but says what it costs", () => {
    // This used to be a hard rejection, and that is what made the headline
    // requirement impossible: a modifier-only chord is a subset of nearly
    // every other shortcut, so almost nothing could be saved. The overlap is
    // real, so it comes back as a caution naming the other action.
    const others = { hangup: "f1+f2", call: "f3+f4" };
    const subset = validateCombo("f1", others);
    expect(subset.status).toBe("ok");
    expect(subset.cautions).toContain("overlap");
    expect(subset.conflict).toEqual({ action: "hangup", combo: "f1+f2" });

    const superset = validateCombo("f3+f4+f5", others);
    expect(superset.status).toBe("ok");
    expect(superset.cautions).toContain("overlap");
    expect(superset.conflict).toEqual({ action: "call", combo: "f3+f4" });
  });

  it("still rejects the very same registration twice", () => {
    // The one ambiguity nothing downstream can resolve: two actions on one
    // chord give the backend a single press and no way to tell them apart.
    const verdict = validateCombo("f1+f2", { hangup: "f1+f2" });
    expect(verdict.status).toBe("error");
    expect(verdict.reason).toBe("collision");
  });

  it("catches an overlap that only appears after normalization", () => {
    // The two spellings are ONE registration on Windows; comparing raw tokens
    // let the user save both and silently killed the second.
    expect(
      validateCombo("ctrl+left_alt+j", { call: "ctrl+right_alt+j" }).reason,
    ).toBe("collision");
  });

  it("allows sharing a modifier with another action (no chord overlap)", () => {
    expect(
      validateCombo("ctrl+shift+h", { call: "ctrl+right_alt+j" }).status,
    ).toBe("ok");
  });
});
