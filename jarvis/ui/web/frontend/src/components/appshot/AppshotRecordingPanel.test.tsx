import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppshotRecordingPanel } from "./AppshotRecordingPanel";
import { loadLocaleChunk } from "@/i18n";
import type { AppshotLibraryItem, AppshotSettings } from "@/lib/appshotApi";

const settings = { enabled: true, recording_hotkey: "ctrl+shift+9" } as AppshotSettings;
const ready = { available: true, detail: "", permission_required: false };
function response(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body } as Response;
}
function video(id: string): AppshotLibraryItem {
  return {
    id: `recording_${id}`, variant: "original", path: `/videos/${id}.mp4`, mime: "video/mp4",
    width: 1920, height: 1080, label: "Screen recording", app_name: "", trigger: "recording",
    taken_at: 100, edited_at: 0, has_edit: false, duration_s: 5,
  };
}
const SCREENSHOT = { ...video("x"), id: "shot", mime: "image/png", path: "/shot.png" };
beforeEach(async () => { await loadLocaleChunk("appshot_editor"); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("AppShot recording controls", () => {
  it("starts selection, keeps a stop control after the master is disabled, and offers the saved video", async () => {
    const onSaved = vi.fn();
    let phase = "idle";
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url === "/api/appshot/library") {
        return response({ items: phase === "saved" ? [video("abc")] : [], max_entries: 500 });
      }
      if (url.endsWith("/start")) phase = "recording";
      if (url.endsWith("/stop")) phase = "saved";
      return response({ phase, id: "abc", message: "", capability: ready,
        recent: phase === "saved" ? [{ id: "abc", created_at: 100 }] : [] });
    }));
    const { rerender } = render(<AppshotRecordingPanel settings={settings} saving={false} onSaved={onSaved} />);
    const button = await screen.findByTestId("appshots-recording-control");
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
    fireEvent.click(button);
    await screen.findByRole("button", { name: "Stop and save" });
    rerender(<AppshotRecordingPanel settings={{ ...settings, enabled: false }} saving={false} onSaved={onSaved} />);
    expect(button.hasAttribute("disabled")).toBe(false);
    fireEvent.click(button);
    const tile = await screen.findByTestId("appshot-library-tile");
    expect(tile.getAttribute("data-media")).toBe("video");
    await waitFor(() => expect(onSaved).toHaveBeenCalledExactlyOnceWith("abc"));
    expect(calls.filter((call) => call.startsWith("POST"))).toEqual([
      "POST /api/appshot/recording/start", "POST /api/appshot/recording/stop",
    ]);
  });

  it("requires permission and requests it only on an explicit click", async () => {
    const fetcher = vi.fn(async () => response({ phase: "idle", id: "", message: "", capability: {
      available: false, detail: "Permission is required", permission_required: true,
    } }));
    vi.stubGlobal("fetch", fetcher);
    render(<AppshotRecordingPanel settings={settings} saving={false} />);
    const allow = await screen.findByRole("button", { name: "Allow screen recording" });
    expect(screen.getByTestId("appshots-recording-control").hasAttribute("disabled")).toBe(true);
    // Only reads so far (status and the video tiles); no permission request.
    expect(fetcher.mock.calls.some((call) => String((call as unknown[])[0]).includes("/permissions/"))).toBe(false);
    fireEvent.click(allow);
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith(
      "/api/permissions/screen_recording/request?dry_run=false", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ feature: "appshot" }),
      },
    ));
  });

  it("surfaces a start failure and never invents a recording state", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url.endsWith("/start")
      ? response({ detail: "No screen" }, 409)
      : response({ phase: "idle", id: "", message: "", capability: ready })));
    render(<AppshotRecordingPanel settings={settings} saving={false} />);
    const button = screen.getByTestId("appshots-recording-control");
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
    fireEvent.click(button);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "No screen");
    expect(screen.queryByRole("button", { name: "Stop and save" })).toBeNull();
  });

  it("shows kept recordings as playable tiles after reopening the page", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url === "/api/appshot/library"
      ? response({ items: [video("older"), SCREENSHOT], max_entries: 500 })
      : response({ phase: "idle", id: "", message: "", capability: ready,
        recent: [{ id: "older", created_at: 100 }] })));
    render(<AppshotRecordingPanel settings={settings} saving={false} />);
    const tiles = await screen.findAllByTestId("appshot-library-tile");
    expect(tiles).toHaveLength(1);
    fireEvent.click(tiles[0].querySelector("button")!);
    const player = await screen.findByTestId("appshot-library-player");
    expect(player.getAttribute("src")).toContain("/library/recording_older/image");
    expect(screen.getByRole("link", { name: "Download video" }).getAttribute("download")).toMatch(/\.mp4$/);
  });
});
