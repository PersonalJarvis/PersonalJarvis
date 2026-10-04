import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useEventStore } from "@/store/events";
import { JarvisXView } from "@/views/jarvisx/JarvisXView";

const SETTINGS = {
  enabled: true,
  hotkeys: {
    region: "ctrl+shift+4",
    window: "ctrl+shift+5",
    fullscreen: "ctrl+shift+3",
    record_region: "ctrl+shift+6",
    record_fullscreen: "",
    stop_recording: "ctrl+shift+7",
  },
  thumbnail_persist: false,
  thumbnail_dismiss_s: 30,
  save_dir: "/home/me/Pictures/Jarvis X",
  copy_to_clipboard: true,
  sound: true,
  effect: true,
  recording_available: true,
  recording_detail: "",
  shortcuts: {
    region: { hotkey: "ctrl+shift+4", armed: true, detail: "" },
    window: { hotkey: "ctrl+shift+5", armed: false, detail: "taken by another app" },
  },
};

const ITEMS = [
  {
    id: "old",
    kind: "image",
    mode: "window",
    created_at: "2026-09-01T10:00:00Z",
    width: 800,
    height: 600,
    duration_s: null,
    filename: "old.png",
    url: "/api/jarvisx/items/old/file",
    thumb_url: "/api/jarvisx/items/old/thumb",
    edited_url: null,
  },
  {
    id: "vid",
    kind: "video",
    mode: "fullscreen",
    created_at: "2026-09-29T10:00:00Z",
    width: 1920,
    height: 1080,
    duration_s: 83,
    filename: "vid.mp4",
    url: "/api/jarvisx/items/vid/file",
    thumb_url: "/api/jarvisx/items/vid/thumb",
    edited_url: null,
  },
];

function json(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

describe("JarvisXView", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let items: typeof ITEMS;

  beforeEach(() => {
    items = [...ITEMS];
    useEventStore.setState({ events: [], toasts: [], assistantName: "Jarvis" });
    fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/jarvisx/settings" && init?.method === "PUT") {
        const patch = JSON.parse(String(init.body));
        return json({ ...SETTINGS, ...patch, hotkeys: { ...SETTINGS.hotkeys, ...(patch.hotkeys ?? {}) } });
      }
      if (url === "/api/jarvisx/settings") return json(SETTINGS);
      if (url.startsWith("/api/jarvisx/items?")) return json({ items });
      if (url === "/api/jarvisx/record/status") return json({ recording: false, mode: null, elapsed_s: 0 });
      if (url === "/api/jarvisx/items/old" && init?.method === "DELETE") {
        items = items.filter((i) => i.id !== "old");
        return json({ ok: true });
      }
      return json({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("lists captures newest first with a video duration", async () => {
    render(<JarvisXView />);
    const cards = await screen.findAllByTestId("jarvisx-item");
    expect(cards).toHaveLength(2);
    expect(cards[0].textContent).toContain("1:23");
    expect(screen.getByTestId("jarvisx-capture-region")).toBeDefined();
    expect(screen.getByTestId("jarvisx-record-region")).toBeDefined();
  });

  it("deletes only after the in-app confirmation", async () => {
    const confirmSpy = vi.fn();
    vi.stubGlobal("confirm", confirmSpy);
    render(<JarvisXView />);
    await screen.findAllByTestId("jarvisx-item");
    const deleteButtons = screen.getAllByTestId("jarvisx-item-delete");
    fireEvent.click(deleteButtons[1]);
    expect(screen.getByTestId("jarvisx-confirm")).toBeDefined();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByTestId("jarvisx-confirm-ok"));
    await waitFor(() => expect(screen.getAllByTestId("jarvisx-item")).toHaveLength(1));
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it("refreshes the library when a capture event arrives", async () => {
    render(<JarvisXView />);
    await screen.findAllByTestId("jarvisx-item");
    const before = fetchMock.mock.calls.filter(([url]) => String(url).startsWith("/api/jarvisx/items?")).length;
    act(() => {
      useEventStore.getState().pushEvent({
        id: "e1",
        name: "JarvisXItemCreated",
        ts: Date.now(),
        payload: { id: "new", kind: "image", mode: "region" },
      });
    });
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([url]) => String(url).startsWith("/api/jarvisx/items?")).length).toBe(
        before + 1,
      ),
    );
  });

  it("shows a recording indicator from the websocket event", async () => {
    render(<JarvisXView />);
    await screen.findAllByTestId("jarvisx-item");
    act(() => {
      useEventStore.getState().pushEvent({
        id: "e2",
        name: "JarvisXRecordingChanged",
        ts: Date.now(),
        payload: { recording: true, mode: "region", elapsed_s: 5 },
      });
    });
    expect(await screen.findByTestId("jarvisx-recording")).toBeDefined();
    expect(screen.getByTestId("jarvisx-record-stop")).toBeDefined();
  });

  it("hides the seconds field while the thumbnail stays until dismissed", async () => {
    render(<JarvisXView />);
    fireEvent.click(await screen.findByText("Settings"));
    const persist = await screen.findByTestId("jarvisx-persist");
    expect(screen.getByTestId("jarvisx-dismiss-row")).toBeDefined();
    fireEvent.click(persist);
    await waitFor(() => expect(screen.queryByTestId("jarvisx-dismiss-row")).toBeNull());
    const put = fetchMock.mock.calls.find(
      ([url, init]) => url === "/api/jarvisx/settings" && init?.method === "PUT",
    );
    expect(JSON.parse(String(put![1].body))).toEqual({ thumbnail_persist: true });
    fireEvent.click(screen.getByTestId("jarvisx-persist"));
    await waitFor(() => expect(screen.getByTestId("jarvisx-dismiss-row")).toBeDefined());
  });

  it("saves a clamped dismiss delay", async () => {
    render(<JarvisXView />);
    fireEvent.click(await screen.findByText("Settings"));
    const input = await screen.findByTestId("jarvisx-dismiss-input");
    fireEvent.change(input, { target: { value: "999" } });
    fireEvent.blur(input);
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([url, init]) => url === "/api/jarvisx/settings" && init?.method === "PUT",
      );
      expect(JSON.parse(String(put![1].body))).toEqual({ thumbnail_dismiss_s: 300 });
    });
  });

  it("shows whether each shortcut is armed", async () => {
    render(<JarvisXView />);
    fireEvent.click(await screen.findByText("Settings"));
    expect(await screen.findByTestId("jarvisx-hotkey-armed-region")).toBeDefined();
    expect(screen.getByTestId("jarvisx-hotkey-unarmed-window").textContent).toContain("taken by another app");
  });
});
