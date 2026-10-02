import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SHORTCUTS_TIP_SEEN_KEY, claimShortcutsTip, shortcutsTipSeen } from "./shortcutsTip";

const MAC_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15";
const WIN_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)";

function setUserAgent(ua: string) {
  vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(ua);
}
function setEmbedded(on: boolean) {
  (window as unknown as { __JARVIS_EMBEDDED_DESKTOP?: boolean }).__JARVIS_EMBEDDED_DESKTOP = on;
}
function keybinds(state: string) {
  const fetchMock = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => ({ shortcuts_status: { state, detail: "" } }),
  }) as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  window.localStorage.clear();
  setUserAgent(MAC_UA);
  setEmbedded(true);
});
afterEach(() => {
  setEmbedded(false);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("claimShortcutsTip", () => {
  it("offers the tip once on a Mac that has not allowed global shortcuts, then never again", async () => {
    const fetchMock = keybinds("needs_input_monitoring");

    expect(await claimShortcutsTip()).toBe(true);
    expect(shortcutsTipSeen()).toBe(true);
    expect(await claimShortcutsTip()).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("two composers asking in the same tick still show one tip", async () => {
    keybinds("needs_input_monitoring");
    const [first, second] = await Promise.all([claimShortcutsTip(), claimShortcutsTip()]);
    expect([first, second].filter(Boolean)).toHaveLength(1);
  });

  it("offers nothing, and reads nothing, off macOS", async () => {
    setUserAgent(WIN_UA);
    const fetchMock = keybinds("needs_input_monitoring");
    expect(await claimShortcutsTip()).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("offers nothing to a remote browser (its shortcuts are not the host's)", async () => {
    setEmbedded(false);
    const fetchMock = keybinds("needs_input_monitoring");
    expect(await claimShortcutsTip()).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("offers nothing when the shortcuts already work, and keeps the chance for later", async () => {
    keybinds("ready");
    expect(await claimShortcutsTip()).toBe(false);
    expect(shortcutsTipSeen()).toBe(false);
  });

  it("stays quiet when the status cannot be read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new Error("offline"))));
    expect(await claimShortcutsTip()).toBe(false);
    expect(shortcutsTipSeen()).toBe(false);
  });

  it("stays quiet when storage is unusable instead of risking a tip on every dictation", async () => {
    keybinds("needs_input_monitoring");
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(await claimShortcutsTip()).toBe(false);
  });

  it("uses its own versioned key", () => {
    expect(SHORTCUTS_TIP_SEEN_KEY).toBe("jarvis.shortcuts.tip.seen.v1");
  });
});
