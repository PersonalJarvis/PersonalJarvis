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

  it("refuses a lone modifier and ignores Escape", () => {
    expect(chordFromCodes(["ShiftLeft"])).toEqual({ problem: "modifier_only" });
    expect(chordFromCodes(["ShiftLeft", "ControlLeft"])).toEqual({ problem: "modifier_only" });
    expect(chordFromCodes(["Escape"])).toEqual({ problem: "empty" });
  });

  it("names the family of a gesture and nothing else", () => {
    expect(gestureFamily("shift+shift")).toBe("shift");
    expect(gestureFamily("ctrl+shift+s")).toBeNull();
  });
});
