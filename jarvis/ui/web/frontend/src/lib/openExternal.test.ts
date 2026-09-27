import { afterEach, describe, expect, it, vi } from "vitest";

import { openExternalUrl } from "@/lib/openExternal";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("openExternalUrl", () => {
  it("reports true when the backend bridge opens the browser", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ opened: true }),
      })) as unknown as typeof fetch,
    );
    const winOpen = vi.spyOn(window, "open").mockImplementation(() => null);
    await expect(openExternalUrl("https://example.test/auth")).resolves.toBe(true);
    expect(winOpen).not.toHaveBeenCalled();
  });

  it("falls back to a tab and reports whether one was created", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ opened: false }),
      })) as unknown as typeof fetch,
    );
    const winOpen = vi
      .spyOn(window, "open")
      .mockImplementation(() => ({}) as unknown as Window);
    await expect(openExternalUrl("https://example.test/auth")).resolves.toBe(true);
    expect(winOpen).toHaveBeenCalledWith(
      "https://example.test/auth",
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("reports false when the bridge misses and the popup blocker eats the tab", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ opened: false }),
      })) as unknown as typeof fetch,
    );
    vi.spyOn(window, "open").mockImplementation(() => null);
    await expect(openExternalUrl("https://example.test/auth")).resolves.toBe(false);
  });
});
