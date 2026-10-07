import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { copyAppshotPng } from "@/lib/appshotClipboard";

const blob = new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" });

function reply(status: number, body: unknown = {}): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

describe("copyAppshotPng", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "ClipboardItem",
      class {
        constructor(public items: Record<string, Blob>) {}
      },
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("copies natively on the desktop and never touches the browser clipboard", async () => {
    const fetchImpl = vi.fn(async () => reply(200, { copied: true }));
    const clipboard = { write: vi.fn(async () => undefined) };

    await expect(
      copyAppshotPng(blob, { native: true, timeoutMessage: "t", fetchImpl, clipboard }),
    ).resolves.toBe("native");

    expect(fetchImpl).toHaveBeenCalledWith("/api/appshot/clipboard", expect.objectContaining({ method: "POST" }));
    expect(clipboard.write).not.toHaveBeenCalled();
  });

  it("falls back to the browser clipboard when the OS refuses", async () => {
    const fetchImpl = vi.fn(async () => reply(503, { detail: "native-clipboard-unavailable" }));
    const clipboard = { write: vi.fn(async () => undefined) };

    await expect(
      copyAppshotPng(blob, { native: true, timeoutMessage: "t", fetchImpl, clipboard }),
    ).resolves.toBe("browser");
    expect(clipboard.write).toHaveBeenCalledTimes(1);
  });

  it("uses only the browser clipboard in a browser", async () => {
    const fetchImpl = vi.fn();
    const clipboard = { write: vi.fn(async () => undefined) };

    await copyAppshotPng(blob, { native: false, timeoutMessage: "t", fetchImpl, clipboard });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("gives up on a browser clipboard that never answers", async () => {
    const clipboard = { write: vi.fn(() => new Promise<void>(() => undefined)) };

    await expect(
      copyAppshotPng(blob, { native: false, timeoutMessage: "no answer", clipboard, timeoutMs: 10 }),
    ).rejects.toThrow("no answer");
  });

  it("names the native reason when no clipboard is left", async () => {
    const fetchImpl = vi.fn(async () => reply(503, { detail: "native-clipboard-unavailable" }));

    await expect(
      copyAppshotPng(blob, { native: true, timeoutMessage: "t", fetchImpl, clipboard: null }),
    ).rejects.toThrow("native-clipboard-unavailable");
  });
});
