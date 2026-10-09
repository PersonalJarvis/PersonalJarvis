import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useEventStore } from "@/store/events";
import { AppshotEditorWindow, recordingIdFromUrl, type EditorWindowDeps } from "@/views/AppshotEditorWindow";
import { AppshotVideoEditor, formatVideoTime, moveTrimEdge } from "@/views/AppshotVideoEditor";

/**
 * The video editor: a played recording opens in the editor window, plays
 * and trims there, and Save writes the chosen part at the chosen speed.
 */

const RECORDING = "a".repeat(32);

function json(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

/** The desktop shell's capabilities decide between a native save and a browser download. */
async function capabilitiesLoaded() {
  // The capability check waits out the boot stagger (4 s) first; then it is cached.
  await waitFor(() => expect(screen.getByTestId("appshot-video-editor").dataset.native).toBe("true"), {
    timeout: 6000,
  });
}

function loadVideo(duration = 20) {
  const video = screen.getByTestId("appshot-video") as HTMLVideoElement;
  Object.defineProperty(video, "duration", { configurable: true, value: duration });
  Object.defineProperty(video, "videoWidth", { configurable: true, value: 1280 });
  Object.defineProperty(video, "videoHeight", { configurable: true, value: 720 });
  fireEvent(video, new Event("loadedmetadata"));
  return video;
}

describe("the video editor", () => {
  beforeEach(() => {
    useEventStore.setState({ events: [], toasts: [], assistantName: "George" });
    vi.stubGlobal("fetch", vi.fn(async () => json({ native_file_actions: true, platform: "win32" })));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  it("formats times and keeps trims inside the video", () => {
    expect(formatVideoTime(0)).toBe("0:00.0");
    expect(formatVideoTime(75.36)).toBe("1:15.3");
    expect(formatVideoTime(3725, false)).toBe("1:02:05");
    const trim = { start: 0, end: 10 };
    expect(moveTrimEdge(trim, "start", 9.9, 10)).toEqual({ start: 9.5, end: 10 });
    expect(moveTrimEdge(trim, "end", -3, 10)).toEqual({ start: 0, end: 0.5 });
    expect(moveTrimEdge(trim, "end", 42, 10)).toEqual({ start: 0, end: 10 });
  });

  it("only accepts a recording id from the window URL", () => {
    expect(recordingIdFromUrl(`?recording=${RECORDING}`)).toBe(RECORDING);
    expect(recordingIdFromUrl("?recording=../../etc")).toBe("");
  });

  it("opens on a recording named by the shell and closes without a corner card", async () => {
    window.history.replaceState(null, "", "/?view=appshot-editor&solo=1");
    const calls: string[] = [];
    const deps: EditorWindowDeps = {
      returnCard: async () => void calls.push("card"),
      closeWindow: async () => void calls.push("close"),
    };
    render(<AppshotEditorWindow deps={deps} />);
    act(() => {
      expect(window.__jarvisOpenRecording?.(RECORDING)).toBe(true);
    });
    expect(await screen.findByTestId("appshot-video-editor")).toBeTruthy();
    fireEvent.click(screen.getByTestId("appshot-video-close"));
    await waitFor(() => expect(calls).toEqual(["close"]));
    expect(screen.queryByTestId("appshot-video-editor")).toBeNull();
  });

  it("saves the trimmed part at the chosen speed as a new recording", { timeout: 10_000 }, async () => {
    const requests: Array<[string, string | undefined]> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        requests.push([url, init?.body as string | undefined]);
        if (url.endsWith("/export")) return json({ id: "b".repeat(32) });
        if (url.endsWith("/save")) return json({ path: "C:/Downloads/clip.mp4", filename: "clip.mp4" });
        return json({ native_file_actions: true, platform: "win32" });
      }),
    );
    render(<AppshotVideoEditor recordingId={RECORDING} onClose={() => undefined} />);
    const video = loadVideo(20);
    video.currentTime = 4;
    fireEvent.keyDown(window, { key: "i" });
    video.currentTime = 12;
    fireEvent.keyDown(window, { key: "o" });
    fireEvent.click(await screen.findByRole("button", { name: /^2×$/ }));
    expect(video.playbackRate).toBe(2);
    await waitFor(() => expect(screen.getByTestId("appshot-video-clip-length").textContent).toContain("0:04"));

    await capabilitiesLoaded();
    fireEvent.keyDown(window, { key: "s", ctrlKey: true });
    await waitFor(() => expect(requests.some(([url]) => url.endsWith(`/${"b".repeat(32)}/save`))).toBe(true));
    const exported = requests.find(([url]) => url.endsWith(`/${RECORDING}/export`));
    expect(JSON.parse(exported?.[1] ?? "{}")).toEqual({ start_s: 4, end_s: 12, speed: 2 });
  });

  it("saves an unchanged video without writing a copy first", { timeout: 10_000 }, async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        urls.push(url);
        if (url.endsWith("/save")) return json({ path: "C:/Downloads/v.mp4", filename: "v.mp4" });
        return json({ native_file_actions: true, platform: "win32" });
      }),
    );
    render(<AppshotVideoEditor recordingId={RECORDING} onClose={() => undefined} />);
    loadVideo(8);
    await capabilitiesLoaded();
    fireEvent.click(screen.getByTestId("appshot-video-save"));
    await waitFor(() => expect(urls.some((url) => url.endsWith(`/${RECORDING}/save`))).toBe(true));
    expect(urls.some((url) => url.endsWith("/export"))).toBe(false);
  });

  it("says so when the video is gone", async () => {
    render(<AppshotVideoEditor recordingId={RECORDING} onClose={() => undefined} />);
    fireEvent(screen.getByTestId("appshot-video"), new Event("error"));
    expect(await screen.findByTestId("appshot-video-failed")).toBeTruthy();
  });
});
