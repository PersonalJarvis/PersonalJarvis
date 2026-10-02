import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  SHORTCUTS_NOTE_SURFACES,
  clearShortcutsNoteDismissals,
  dismissShortcutsNote,
  isShortcutsNoteDismissed,
  shortcutsNoteKey,
} from "./shortcutsNoteDismissal";

beforeEach(() => window.localStorage.clear());
afterEach(() => window.localStorage.clear());

describe("shortcutsNoteDismissal", () => {
  it("remembers a closing per surface, with a key that names no episode", () => {
    expect(isShortcutsNoteDismissed("dictation")).toBe(false);

    dismissShortcutsNote("dictation");

    expect(isShortcutsNoteDismissed("dictation")).toBe(true);
    expect(isShortcutsNoteDismissed("shortcuts")).toBe(false);
    expect(shortcutsNoteKey("dictation")).toBe("jarvis.shortcuts.note.dismissed.v1.dictation");
  });

  it("forgets every surface at once", () => {
    for (const surface of SHORTCUTS_NOTE_SURFACES) dismissShortcutsNote(surface);

    clearShortcutsNoteDismissals();

    for (const surface of SHORTCUTS_NOTE_SURFACES) expect(isShortcutsNoteDismissed(surface), surface).toBe(false);
  });

  it("never throws without storage: unreadable reads as 'not closed', writes and clears are silent", () => {
    const original = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new DOMException("blocked", "SecurityError");
      },
    });
    try {
      expect(isShortcutsNoteDismissed("shortcuts")).toBe(false);
      expect(() => dismissShortcutsNote("shortcuts")).not.toThrow();
      expect(() => clearShortcutsNoteDismissals()).not.toThrow();
    } finally {
      if (original) Object.defineProperty(window, "localStorage", original);
    }
  });
});
