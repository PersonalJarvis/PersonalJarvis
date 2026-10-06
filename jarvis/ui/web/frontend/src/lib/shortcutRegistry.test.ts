/**
 * The registry must not drift from the code that implements the chords.
 *
 * Every chord is now read from a live setting, so the risky half is the
 * DEFAULTS: the shipped bindings must still mean what the matchers answer to.
 * They are replayed here through the real matchers — the terminal zoom, the
 * overview trigger and the IDE leader — on both platforms.
 */
import { describe, expect, it } from "vitest";
import { defaultTerminalZoomBindings, zoomIntentForBindings } from "@/components/agentic/terminalZoom";
import { isLeaderChord, leaderPassthrough } from "@/components/agentic/ideHotkeys";
import { APP_CHORD_IDS, defaultAppChords } from "./appChords";
import { SHORTCUTS, SHORTCUT_AREAS, keyLabel, shortcutsForArea } from "./shortcutRegistry";
import { shouldOpenShortcutOverlay } from "./shortcutOverlayTrigger";
import { eventMatchesChord, defaultQuickSwitchCombo } from "./quickSwitchChord";

/** A keydown as the matchers see it. */
function keyEvent(key: string, code: string, mods: { ctrl?: boolean; meta?: boolean; shift?: boolean } = {}) {
  return {
    key,
    code,
    ctrlKey: Boolean(mods.ctrl),
    metaKey: Boolean(mods.meta),
    altKey: false,
    shiftKey: Boolean(mods.shift),
  };
}

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

describe("the default chords agree with the matchers that implement them", () => {
  it("lists every in-app chord in the registry, read from its setting", () => {
    const settings = SHORTCUTS.filter((s) => s.kind === "app").map((s) => s.setting);
    for (const id of APP_CHORD_IDS) expect(settings).toContain(id);
    for (const shortcut of SHORTCUTS) expect(shortcut).not.toHaveProperty("keys");
  });

  for (const isMac of [true, false]) {
    it(`zooms the terminal text on ${isMac ? "macOS" : "PC"} with both spellings`, () => {
      const mod = isMac ? { meta: true } : { ctrl: true };
      const bindings = defaultTerminalZoomBindings(isMac);
      const intent = (key: string, code: string, shift = false) =>
        zoomIntentForBindings(keyEvent(key, code, { ...mod, shift }), { isMac, bindings });
      expect(intent("+", "BracketRight")).toBe("in");
      expect(intent("=", "Equal")).toBe("in");
      expect(intent("-", "Minus")).toBe("out");
      expect(intent("_", "Minus", true)).toBe("out");
      expect(intent("0", "Digit0")).toBe("reset");
    });
  }

  it("answers a recorded terminal chord and drops a removed one", () => {
    const bindings = { in: "alt+up", out: "", reset: "ctrl+0" };
    const altUp = { ...keyEvent("ArrowUp", "ArrowUp"), altKey: true };
    expect(zoomIntentForBindings(altUp, { isMac: false, bindings })).toBe("in");
    expect(zoomIntentForBindings(keyEvent("-", "Minus", { ctrl: true }), { isMac: false, bindings })).toBeNull();
  });

  it("opens the overview on a bare `?` by default, and on a recorded chord", () => {
    const base = { ctrlKey: false, metaKey: false, altKey: false, defaultPrevented: false, target: null };
    expect(shouldOpenShortcutOverlay({ ...base, key: "?" }, defaultAppChords().shortcut_overlay)).toBe(true);
    const f1 = { ...base, key: "F1", code: "F1" };
    expect(shouldOpenShortcutOverlay(f1, "f1")).toBe(true);
    expect(shouldOpenShortcutOverlay({ ...base, key: "?" }, "f1")).toBe(false);
    expect(shouldOpenShortcutOverlay({ ...base, key: "?" }, "")).toBe(false);
  });

  it("opens the IDE key menu on Ctrl+B by default, and passes its control code through", () => {
    const leader = defaultAppChords().ide_menu;
    expect(isLeaderChord(keyEvent("b", "KeyB", { ctrl: true }), leader)).toBe(true);
    expect(leaderPassthrough(leader)).toBe("\x02");
    expect(isLeaderChord(keyEvent("b", "KeyB", { ctrl: true }), "ctrl+g")).toBe(false);
    expect(isLeaderChord(keyEvent("g", "KeyG", { ctrl: true }), "ctrl+g")).toBe(true);
    expect(leaderPassthrough("ctrl+g")).toBe("\x07");
    expect(leaderPassthrough("alt+b")).toBeNull();
  });
});

describe("the quick switcher entry", () => {
  it("is read from the live setting, never spelled out", () => {
    const entry = shortcutsForArea("workspace").find(
      (s) => s.labelKey === "shortcut_overlay.workspace.quick_switch",
    );
    expect(entry?.kind).toBe("app");
    expect(entry).not.toHaveProperty("keys");
  });

  it("defaults to a chord the matcher accepts on both platforms", () => {
    const base = { code: "Space", ctrlKey: false, metaKey: false, altKey: false, shiftKey: false };
    expect(eventMatchesChord({ ...base, ctrlKey: true }, defaultQuickSwitchCombo("pc"))).toBe(true);
    expect(eventMatchesChord({ ...base, altKey: true }, defaultQuickSwitchCombo("mac"))).toBe(true);
  });
});

describe("keyLabel", () => {
  it("prints Mac glyphs for saved modifier names", () => {
    expect(keyLabel("Alt", true)).toBe("⌥");
    expect(keyLabel("Ctrl", true)).toBe("⌃");
    expect(keyLabel("Cmd", true)).toBe("⌘");
  });

  it("draws the platform modifier the way each keyboard prints it", () => {
    expect(keyLabel("Mod", true)).toBe("⌘");
    expect(keyLabel("Mod", false)).toBe("Ctrl");
  });

  it("passes every other token through untouched", () => {
    expect(keyLabel("F9", true)).toBe("F9");
    expect(keyLabel("Alt", false)).toBe("Alt");
    expect(keyLabel("+", false)).toBe("+");
  });
});
