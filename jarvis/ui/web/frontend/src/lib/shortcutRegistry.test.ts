/**
 * The registry must not drift from the code that implements the chords.
 *
 * The fixed entries are the risky half: they spell out keys that a matcher
 * elsewhere decides on. So every fixed chord declared for the workspace area is
 * replayed here through `zoomIntentFor` — the real matcher — and must produce
 * the intent its label claims. Change the matcher without changing the registry
 * and this fails, which is the whole reason the duplication is acceptable.
 */
import { describe, expect, it } from "vitest";
import { zoomIntentFor } from "@/components/agentic/terminalZoom";
import {
  SHORTCUTS,
  SHORTCUT_AREAS,
  keyLabel,
  shortcutsForArea,
  type FixedShortcut,
} from "./shortcutRegistry";
import { shouldOpenShortcutOverlay } from "./shortcutOverlayTrigger";

/** Turn a declared chord into the event the matcher would see. */
function eventFor(keys: string[], isMac: boolean) {
  const mod = keys.includes("Mod");
  const key = keys[keys.length - 1];
  return {
    key,
    code: "",
    ctrlKey: mod && !isMac,
    metaKey: mod && isMac,
    altKey: false,
  };
}

const ZOOM_EXPECTATIONS: Record<string, "in" | "out" | "reset"> = {
  "shortcut_overlay.workspace.zoom_in": "in",
  "shortcut_overlay.workspace.zoom_out": "out",
  "shortcut_overlay.workspace.zoom_reset": "reset",
};

describe("registry integrity", () => {
  it("declares every area it groups by, and groups nothing else", () => {
    const declared = new Set(SHORTCUTS.map((s) => s.area));
    expect([...declared].sort()).toEqual([...SHORTCUT_AREAS].sort());
  });

  it("never spells out a chord for a rebindable shortcut", () => {
    for (const shortcut of SHORTCUTS) {
      if (shortcut.kind !== "rebindable") continue;
      // A `keys` field here would be a copy of a configurable value — exactly
      // the staleness this design exists to avoid.
      expect(shortcut).not.toHaveProperty("keys");
      expect(shortcut.action).toBeTruthy();
    }
  });

  it("gives every entry a label key", () => {
    for (const shortcut of SHORTCUTS) {
      expect(shortcut.labelKey.startsWith("shortcut_overlay.")).toBe(true);
    }
  });
});

describe("fixed chords agree with the matcher that implements them", () => {
  const zoomEntries = shortcutsForArea("workspace").filter(
    (s): s is FixedShortcut =>
      s.kind === "fixed" && s.labelKey in ZOOM_EXPECTATIONS,
  );

  it("covers all three zoom steps", () => {
    expect(zoomEntries).toHaveLength(3);
  });

  for (const isMac of [true, false]) {
    it(`resolves on ${isMac ? "macOS" : "PC"} exactly as declared`, () => {
      for (const entry of zoomEntries) {
        const expected = ZOOM_EXPECTATIONS[entry.labelKey];
        expect(zoomIntentFor(eventFor(entry.keys, isMac), { isMac })).toBe(
          expected,
        );
        for (const alt of entry.alternateKeys ?? []) {
          expect(zoomIntentFor(eventFor(alt, isMac), { isMac })).toBe(expected);
        }
      }
    });
  }

  it("declares the `?` chord that actually opens the overlay", () => {
    const entry = shortcutsForArea("workspace").find(
      (s) => s.labelKey === "shortcut_overlay.workspace.open_overlay",
    ) as FixedShortcut;
    expect(entry.keys).toEqual(["?"]);
    expect(
      shouldOpenShortcutOverlay({
        key: entry.keys[0],
        ctrlKey: false,
        metaKey: false,
        altKey: false,
        defaultPrevented: false,
        target: null,
      }),
    ).toBe(true);
  });
});

describe("keyLabel", () => {
  it("draws the platform modifier the way each keyboard prints it", () => {
    expect(keyLabel("Mod", true)).toBe("⌘");
    expect(keyLabel("Mod", false)).toBe("Ctrl");
  });

  it("passes every other token through untouched", () => {
    expect(keyLabel("F9", true)).toBe("F9");
    expect(keyLabel("+", false)).toBe("+");
  });
});
