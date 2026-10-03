import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clampDismissSeconds,
  copyJarvisXItem,
  errorDetail,
  fetchJarvisXItems,
  formatDuration,
  isJarvisXEvent,
  jarvisXEditorUrl,
  normalizeRecordStatus,
  saveJarvisXEdited,
  saveJarvisXSettings,
  withVersion,
  type JarvisXItem,
} from "@/lib/jarvisxApi";

function json(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

function item(id: string, created_at: string): JarvisXItem {
  return {
    id,
    kind: "image",
    mode: "region",
    created_at,
    width: 10,
    height: 10,
    duration_s: null,
    filename: `${id}.png`,
    url: `/api/jarvisx/items/${id}/file`,
    thumb_url: `/api/jarvisx/items/${id}/thumb`,
    edited_url: null,
  };
}

describe("jarvisxApi", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("lists items newest first", async () => {
    fetchMock.mockResolvedValue(
      json({ items: [item("old", "2026-09-01T10:00:00Z"), item("new", "2026-09-29T10:00:00Z")] }),
    );
    const items = await fetchJarvisXItems(50);
    expect(fetchMock).toHaveBeenCalledWith("/api/jarvisx/items?limit=50", undefined);
    expect(items.map((i) => i.id)).toEqual(["new", "old"]);
  });

  it("sends a partial settings patch and surfaces a 422 reason", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Ctrl+Alt+Del is reserved by the system." }, 422));
    await expect(saveJarvisXSettings({ hotkeys: { region: "ctrl+alt+delete" } })).rejects.toThrow(
      "Ctrl+Alt+Del is reserved by the system.",
    );
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/jarvisx/settings");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ hotkeys: { region: "ctrl+alt+delete" } });
  });

  it("uploads the edited picture as a raw PNG body", async () => {
    fetchMock.mockResolvedValue(json(item("a", "2026-09-29T10:00:00Z")));
    const png = new Blob(["x"], { type: "image/png" });
    await saveJarvisXEdited("a b", png);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/jarvisx/items/a%20b/edited");
    expect(init.method).toBe("PUT");
    expect(init.headers["content-type"]).toBe("image/png");
    expect(init.body).toBe(png);
  });

  it("asks the backend to copy the edited or the original version", async () => {
    fetchMock.mockResolvedValue(json({ ok: true, message: "" }));
    await copyJarvisXItem("a", true);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ edited: true });
  });

  it("reads FastAPI's string and list error bodies", () => {
    expect(errorDetail({ detail: "nope" })).toBe("nope");
    expect(errorDetail({ detail: [{ msg: "bad hotkey" }, { msg: "again" }] })).toBe("bad hotkey again");
    expect(errorDetail(null)).toBe("");
  });

  it("normalises recording status from a body or an event payload", () => {
    expect(normalizeRecordStatus({ recording: true, mode: "region", elapsed_s: 4.2 })).toEqual({
      recording: true,
      mode: "region",
      elapsed_s: 4.2,
    });
    expect(normalizeRecordStatus({ mode: "sideways" })).toEqual({ recording: false, mode: null, elapsed_s: 0 });
    expect(normalizeRecordStatus(undefined)).toEqual({ recording: false, mode: null, elapsed_s: 0 });
  });

  it("formats durations, clamps the dismiss delay and builds URLs", () => {
    expect(formatDuration(83.4)).toBe("1:23");
    expect(formatDuration(3723)).toBe("1:02:03");
    expect(formatDuration(null)).toBe("");
    expect(clampDismissSeconds(1)).toBe(3);
    expect(clampDismissSeconds(1000)).toBe(300);
    expect(clampDismissSeconds(Number.NaN)).toBe(30);
    expect(jarvisXEditorUrl("a/b")).toBe("/?view=jarvisx-editor&solo=1&item=a%2Fb");
    expect(withVersion("/x?y=1", 2)).toBe("/x?y=1&v=2");
    expect(isJarvisXEvent("JarvisXItemCreated")).toBe(true);
    expect(isJarvisXEvent("AppshotTaken")).toBe(false);
  });
});
