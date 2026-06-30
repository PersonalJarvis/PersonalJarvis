import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { saveOrDownload } from "./clipboard";

/** jsdom does not define URL.createObjectURL at all, so assign the methods
 *  directly (spyOn would fail) + stub the anchor click so the browser-download
 *  path (downloadAs/downloadBlob) runs without throwing. */
function stubBrowserDownload() {
  const u = URL as unknown as Record<string, unknown>;
  u.createObjectURL = vi.fn(() => "blob:stub");
  u.revokeObjectURL = vi.fn();
  const clickSpy = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {});
  return { clickSpy };
}

describe("saveOrDownload", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("posts base64 to the backend on desktop and returns the saved path", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ saved_path: "/home/u/Downloads/note.txt" }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const saved = await saveOrDownload({
      filename: "note.txt",
      text: "hi",
      native: true,
    });

    expect(saved).toBe("/home/u/Downloads/note.txt");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("/api/downloads/save");
    const body = JSON.parse(init.body as string) as {
      filename: string;
      content_b64: string;
    };
    expect(body.filename).toBe("note.txt");
    expect(body.content_b64.length).toBeGreaterThan(0);
  });

  it("uses the browser download (no backend call) when not native", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const { clickSpy } = stubBrowserDownload();

    const saved = await saveOrDownload({
      filename: "note.txt",
      text: "hi",
      native: false,
    });

    expect(saved).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalledTimes(1);
  });

  it("falls back to the browser download when the backend save fails", async () => {
    const fetchMock = vi.fn(async () => ({ ok: false, status: 500 }));
    vi.stubGlobal("fetch", fetchMock);
    const { clickSpy } = stubBrowserDownload();

    const saved = await saveOrDownload({
      filename: "note.txt",
      text: "hi",
      native: true,
    });

    expect(saved).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
  });
});
