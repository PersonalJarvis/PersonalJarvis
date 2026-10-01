/**
 * Ctrl + Plus / Minus / 0 zoom the desktop window through the shell, and leave
 * the keys alone wherever they belong to someone else.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, renderHook, waitFor } from "@testing-library/react";

import { useAppZoom, useAppZoomSupport } from "./useAppZoom";
import {
  APP_ZOOM_STORAGE_KEY,
  readAppZoomSettings,
  useAppZoomSettings,
} from "@/store/appZoomSettings";

type Host = { __JARVIS_EMBEDDED_DESKTOP?: boolean };

function answer(body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
}

function sentFactors(fetchMock: ReturnType<typeof vi.fn>): number[] {
  return fetchMock.mock.calls.map((call) => JSON.parse(String((call[1] as RequestInit).body)).factor);
}

describe("useAppZoom", () => {
  beforeEach(() => {
    window.localStorage.removeItem(APP_ZOOM_STORAGE_KEY);
    useAppZoomSettings.setState(readAppZoomSettings());
    useAppZoomSupport.setState({ support: "unknown" });
    (window as Host).__JARVIS_EMBEDDED_DESKTOP = true;
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    delete (window as Host).__JARVIS_EMBEDDED_DESKTOP;
  });

  it("zooms the window in on Ctrl+Plus (German key) and out on Ctrl+Minus", async () => {
    const fetchMock = answer({ ok: true, zoom: 1 });
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => useAppZoom());
    await waitFor(() => expect(useAppZoomSupport.getState().support).toBe("native"));

    expect(fireEvent.keyDown(document.body, { key: "+", code: "BracketRight", ctrlKey: true })).toBe(false);
    expect(useAppZoomSettings.getState().level).toBe(1.1);
    fireEvent.keyDown(document.body, { key: "-", code: "Slash", ctrlKey: true });
    fireEvent.keyDown(document.body, { key: "-", code: "Slash", ctrlKey: true });
    expect(useAppZoomSettings.getState().level).toBe(0.9);
    fireEvent.keyDown(document.body, { key: "0", code: "Digit0", ctrlKey: true });
    expect(useAppZoomSettings.getState().level).toBe(1);

    await waitFor(() => expect(sentFactors(fetchMock)).toEqual([1, 1.1, 1, 0.9, 1]));
    expect(readAppZoomSettings().level).toBe(1);
  });

  it("re-applies a remembered level when the window opens", async () => {
    useAppZoomSettings.getState().setLevel(1.25);
    const fetchMock = answer({ ok: true, zoom: 1.25 });
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => useAppZoom());
    await waitFor(() => expect(sentFactors(fetchMock)).toEqual([1.25]));
  });

  it("yields to a terminal pane that claimed the chord first", async () => {
    vi.stubGlobal("fetch", answer({ ok: true, zoom: 1 }));
    const terminal = (event: Event) => event.stopPropagation();
    window.addEventListener("keydown", terminal, true);
    renderHook(() => useAppZoom());

    fireEvent.keyDown(document.body, { key: "+", code: "BracketRight", ctrlKey: true });
    expect(useAppZoomSettings.getState().level).toBe(1);
    window.removeEventListener("keydown", terminal, true);
  });

  it("leaves the keys to a recorder that is capturing", async () => {
    vi.stubGlobal("fetch", answer({ ok: true, zoom: 1 }));
    const recorder = document.createElement("div");
    recorder.setAttribute("data-keybind-recording", "true");
    document.body.appendChild(recorder);
    renderHook(() => useAppZoom());

    expect(fireEvent.keyDown(document.body, { key: "+", code: "BracketRight", ctrlKey: true })).toBe(true);
    expect(useAppZoomSettings.getState().level).toBe(1);
    recorder.remove();
  });

  it("leaves the keys to the browser in a browser tab", () => {
    delete (window as Host).__JARVIS_EMBEDDED_DESKTOP;
    const fetchMock = answer({ ok: false });
    vi.stubGlobal("fetch", fetchMock);
    renderHook(() => useAppZoom());

    expect(fireEvent.keyDown(document.body, { key: "+", code: "BracketRight", ctrlKey: true })).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(useAppZoomSupport.getState().support).toBe("browser");
  });

  it("stops claiming the keys when the engine cannot zoom", async () => {
    vi.stubGlobal("fetch", answer({ ok: false, reason: "zoom_unsupported" }));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    renderHook(() => useAppZoom());
    await waitFor(() => expect(useAppZoomSupport.getState().support).toBe("unsupported"));

    expect(fireEvent.keyDown(document.body, { key: "+", code: "BracketRight", ctrlKey: true })).toBe(true);
    warn.mockRestore();
  });

  it("does nothing when switched off", () => {
    vi.stubGlobal("fetch", answer({ ok: true, zoom: 1 }));
    useAppZoomSettings.getState().setEnabled(false);
    renderHook(() => useAppZoom());

    expect(fireEvent.keyDown(document.body, { key: "+", code: "BracketRight", ctrlKey: true })).toBe(true);
  });
});
