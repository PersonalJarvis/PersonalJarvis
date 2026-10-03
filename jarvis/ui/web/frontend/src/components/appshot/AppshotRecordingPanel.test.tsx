import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppshotRecordingPanel } from "./AppshotRecordingPanel";
import type { AppshotSettings } from "@/lib/appshotApi";

const settings = { enabled: true, recording_hotkey: "ctrl+shift+9" } as AppshotSettings;
const ready = { available: true, detail: "", permission_required: false };
function response(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body } as Response;
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("AppShot recording controls", () => {
  it("starts selection, keeps a stop control after the master is disabled, and offers the saved video", async () => {
    const onSaved = vi.fn();
    let phase = "idle";
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.endsWith("/start")) phase = "recording";
      if (url.endsWith("/stop")) phase = "saved";
      return response({ phase, id: "abc", message: "", capability: ready });
    }));
    const { rerender } = render(<AppshotRecordingPanel settings={settings} saving={false} onShortcut={async () => {}} onSaved={onSaved} />);
    const button = await screen.findByTestId("appshots-recording-control");
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
    fireEvent.click(button);
    await screen.findByRole("button", { name: "Stop and save" });
    rerender(<AppshotRecordingPanel settings={{ ...settings, enabled: false }} saving={false} onShortcut={async () => {}} onSaved={onSaved} />);
    expect(button.hasAttribute("disabled")).toBe(false);
    fireEvent.click(button);
    const link = await screen.findByRole("link", { name: "Download video" });
    expect(link.getAttribute("href")).toBe("/api/appshot/recording/abc/video");
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
    render(<AppshotRecordingPanel settings={settings} saving={false} onShortcut={async () => {}} />);
    const allow = await screen.findByRole("button", { name: "Allow screen recording" });
    expect(screen.getByTestId("appshots-recording-control").hasAttribute("disabled")).toBe(true);
    expect(fetcher.mock.calls).toHaveLength(1);
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
    render(<AppshotRecordingPanel settings={settings} saving={false} onShortcut={async () => {}} />);
    const button = screen.getByTestId("appshots-recording-control");
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
    fireEvent.click(button);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "No screen");
    expect(screen.queryByRole("button", { name: "Stop and save" })).toBeNull();
  });

  it("keeps older recordings downloadable after reopening the page", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ phase: "idle", id: "", message: "",
      capability: ready, recent: [{ id: "older", created_at: 100 }],
    })));
    render(<AppshotRecordingPanel settings={settings} saving={false} onShortcut={async () => {}} />);
    const link = await screen.findByRole("link");
    expect(link.getAttribute("href")).toBe("/api/appshot/recording/older/video");
  });
});
