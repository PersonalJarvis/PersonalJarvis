import { describe, expect, it } from "vitest";

import { chordFromCodes, gestureFamily } from "@/lib/appshotChord";

describe("chordFromCodes", () => {
  it("records both keys of a pair as the two-sided gesture", () => {
    expect(chordFromCodes(["ShiftLeft", "ShiftRight"])).toEqual({ combo: "shift+shift" });
    expect(chordFromCodes(["ControlRight", "ControlLeft"])).toEqual({ combo: "ctrl+ctrl" });
    expect(chordFromCodes(["AltLeft", "AltRight"])).toEqual({ combo: "alt+alt" });
  });

  it("records an ordinary combo with modifiers first", () => {
    expect(chordFromCodes(["KeyS", "ShiftLeft", "ControlLeft"])).toEqual({
      combo: "ctrl+shift+s",
    });
    expect(chordFromCodes(["F9"])).toEqual({ combo: "f9" });
  });

  it("records both Alt keys with the extra left Ctrl reported by Windows AltGr", () => {
    expect(chordFromCodes(["AltLeft", "ControlLeft", "AltRight"]))
      .toEqual({ combo: "alt+alt" });
    expect(chordFromCodes(["ControlLeft", "AltRight", "AltLeft"]))
      .toEqual({ combo: "alt+alt" });
  });

  it("keeps other modifiers and ordinary Ctrl+AltGr shortcuts intact", () => {
    expect(chordFromCodes(["ControlLeft", "AltRight"]))
      .toEqual({ problem: "modifier_only" });
    expect(chordFromCodes(["AltLeft", "AltRight", "ShiftLeft"]))
      .toEqual({ problem: "modifier_only" });
    expect(chordFromCodes(["AltLeft", "AltRight", "ControlRight"]))
      .toEqual({ problem: "modifier_only" });
    expect(chordFromCodes(["ControlLeft", "AltRight", "KeyS"]))
      .toEqual({ combo: "ctrl+right_alt+s" });
  });

  it("refuses a lone modifier and ignores Escape", () => {
    expect(chordFromCodes(["ShiftLeft"])).toEqual({ problem: "modifier_only" });
    expect(chordFromCodes(["ShiftLeft", "ControlLeft"])).toEqual({ problem: "modifier_only" });
    expect(chordFromCodes(["Escape"])).toEqual({ problem: "empty" });
  });

  it("treats AltGraph as the right Alt key, and names a key it cannot bind", () => {
    expect(chordFromCodes(["AltLeft", "AltGraph"])).toEqual({ combo: "alt+alt" });
    expect(chordFromCodes(["BracketLeft"], new Map([["BracketLeft", "\u00fc"]]))).toEqual({
      combo: "\u00fc",
    });
    expect(chordFromCodes(["PrintScreen"])).toEqual({ problem: "unmapped" });
  });

  it("uses the typed letter on QWERTZ while preserving modifier sides", () => {
    expect(chordFromCodes(["ControlLeft", "KeyY"], new Map([["KeyY", "z"]])))
      .toEqual({ combo: "ctrl+z" });
    expect(chordFromCodes(["KeyZ"], new Map([["KeyZ", "y"]])))
      .toEqual({ combo: "y" });
    expect(chordFromCodes(["ShiftLeft", "ShiftRight"], new Map()))
      .toEqual({ combo: "shift+shift" });
  });

  it("names the family of a gesture and nothing else", () => {
    expect(gestureFamily("shift+shift")).toBe("shift");
    expect(gestureFamily("ctrl+shift+s")).toBeNull();
  });
});
