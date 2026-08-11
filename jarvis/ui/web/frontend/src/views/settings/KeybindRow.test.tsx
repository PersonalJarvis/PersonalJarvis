/**
 * The saved-shortcut chip must name the keys the user's OWN keyboard prints.
 *
 * A Mac keycap reads ⌃ ⌥ ⌘ — the words "Ctrl", "Alt" and "Win" appear nowhere
 * on the hardware. Rendering them anyway put a chip on screen naming a key the
 * machine does not have, directly beside the on-screen keyboard drawing that
 * same physical key as ⌥, which is how a shortcut saved on a Mac read back as a
 * Windows one (reported 2026-08-11). ``win`` had already been fixed this way;
 * ``ctrl`` and ``alt`` had not, so this pins BOTH platforms for every modifier
 * rather than the one that happened to be reported.
 *
 * ``formatCombo`` reads the platform ONCE at module scope, so each case has to
 * re-import the module behind a stubbed ``navigator``.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

type Format = (combo: string) => string;

async function formatComboOn(platform: "mac" | "pc"): Promise<Format> {
  vi.resetModules();
  vi.stubGlobal("navigator", {
    platform: platform === "mac" ? "MacIntel" : "Win32",
    userAgent:
      platform === "mac"
        ? "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        : "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
  });
  const mod = await import("./KeybindRow");
  return mod.formatCombo;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("formatCombo modifier vocabulary", () => {
  it("prints Mac glyphs on a Mac", async () => {
    const formatCombo = await formatComboOn("mac");
    expect(formatCombo("ctrl+right_alt+j")).toBe("⌃ + ⌥ + J");
    expect(formatCombo("alt+shift+d")).toBe("⌥ + Shift + D");
    expect(formatCombo("win+space")).toBe("⌘ + Space");
    expect(formatCombo("left_alt+right_ctrl+k")).toBe("⌥ + ⌃ + K");
  });

  it("never leaks a PC modifier word onto a Mac", async () => {
    const formatCombo = await formatComboOn("mac");
    // Every modifier token the backend can hand back, in one sweep: none of
    // them may render a word that a Mac keyboard does not print.
    const combo = formatCombo(
      "ctrl+control+right_ctrl+alt+left_alt+right_alt+altgr+win+meta+cmd+j",
    );
    expect(combo).not.toMatch(/Ctrl|Alt|Win|AltGr/i);
  });

  it("keeps the PC words on a PC", async () => {
    const formatCombo = await formatComboOn("pc");
    expect(formatCombo("ctrl+right_alt+j")).toBe("Ctrl + AltGr + J");
    expect(formatCombo("alt+shift+d")).toBe("Alt + Shift + D");
    expect(formatCombo("win+space")).toBe("Win + Space");
    expect(formatCombo("left_alt+right_ctrl+k")).toBe("Left-Alt + Right-Ctrl + K");
  });

  it("leaves the non-modifier vocabulary alone on both platforms", async () => {
    for (const platform of ["mac", "pc"] as const) {
      const formatCombo = await formatComboOn(platform);
      expect(formatCombo("f3+f4")).toBe("F3 + F4");
      expect(formatCombo("numpad_3")).toBe("Num 3");
      expect(formatCombo("page_up")).toBe("PageUp");
      expect(formatCombo("mouse_x1")).toBe("Mouse Back");
    }
  });
});
