import { afterEach, describe, expect, it, vi } from "vitest";

import {
  NATIVE_DROP_EVENT,
  inDesktopShell,
  waitForNativeDrop,
} from "./nativeDrop";

type Host = { __JARVIS_EMBEDDED_DESKTOP?: boolean; pywebview?: unknown };

afterEach(() => {
  const host = window as unknown as Host;
  delete host.__JARVIS_EMBEDDED_DESKTOP;
  delete host.pywebview;
  vi.useRealTimers();
});

function announce(paths: string[], names: string[]) {
  window.dispatchEvent(
    new CustomEvent(NATIVE_DROP_EVENT, { detail: { paths, names } }),
  );
}

describe("waitForNativeDrop", () => {
  it("answers null at once outside the desktop shell", async () => {
    expect(inDesktopShell()).toBe(false);
    await expect(waitForNativeDrop({ name: "shop" })).resolves.toBeNull();
  });

  it("returns the paths the shell announces for the dropped name", async () => {
    (window as unknown as Host).__JARVIS_EMBEDDED_DESKTOP = true;
    const pending = waitForNativeDrop({ name: "shop" });
    announce(["C:\\Users\\x\\Desktop\\shop"], ["shop"]);
    await expect(pending).resolves.toEqual({
      paths: ["C:\\Users\\x\\Desktop\\shop"],
      names: ["shop"],
    });
  });

  it("ignores an announcement for a different drop", async () => {
    vi.useFakeTimers();
    (window as unknown as Host).pywebview = {};
    const pending = waitForNativeDrop({ name: "shop", timeoutMs: 50 });
    announce(["/elsewhere/notes"], ["notes"]);
    vi.advanceTimersByTime(60);
    await expect(pending).resolves.toBeNull();
  });

  it("gives up after the timeout when the shell stays silent", async () => {
    vi.useFakeTimers();
    (window as unknown as Host).__JARVIS_EMBEDDED_DESKTOP = true;
    const pending = waitForNativeDrop({ timeoutMs: 100 });
    vi.advanceTimersByTime(101);
    await expect(pending).resolves.toBeNull();
  });
});
