/**
 * The zoom bubble names the level, steps with its own buttons, resets, and
 * hides itself unless the pointer rests on it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ZOOM_INDICATOR_MS, ZoomIndicator } from "./ZoomIndicator";
import { showZoomIndicator, useZoomIndicator } from "@/hooks/useAppZoom";
import { APP_ZOOM_STORAGE_KEY, readAppZoomSettings, useAppZoomSettings } from "@/store/appZoomSettings";

describe("ZoomIndicator", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.localStorage.removeItem(APP_ZOOM_STORAGE_KEY);
    useAppZoomSettings.setState(readAppZoomSettings());
    useZoomIndicator.setState({ open: false, seq: 0 });
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("stays hidden until a zoom step", () => {
    render(<ZoomIndicator />);
    expect(screen.queryByTestId("zoom-indicator")).toBeNull();
  });

  it("shows the level and steps with its buttons", () => {
    useAppZoomSettings.getState().setLevel(1.1);
    render(<ZoomIndicator />);
    act(() => showZoomIndicator());
    expect(screen.getByTestId("zoom-indicator-level").textContent).toBe("110 %");

    fireEvent.click(screen.getByTestId("zoom-indicator-in"));
    expect(useAppZoomSettings.getState().level).toBe(1.25);
    fireEvent.click(screen.getByTestId("zoom-indicator-out"));
    fireEvent.click(screen.getByTestId("zoom-indicator-out"));
    expect(screen.getByTestId("zoom-indicator-level").textContent).toBe("100 %");
    expect((screen.getByTestId("zoom-indicator-reset") as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByTestId("zoom-indicator-in"));
    fireEvent.click(screen.getByTestId("zoom-indicator-reset"));
    expect(useAppZoomSettings.getState().level).toBe(1);
  });

  it("hides itself after a moment, and every step restarts the clock", () => {
    render(<ZoomIndicator />);
    act(() => showZoomIndicator());
    act(() => vi.advanceTimersByTime(ZOOM_INDICATOR_MS - 500));
    act(() => showZoomIndicator());
    act(() => vi.advanceTimersByTime(ZOOM_INDICATOR_MS - 500));
    expect(screen.getByTestId("zoom-indicator")).toBeTruthy();
    act(() => vi.advanceTimersByTime(600));
    expect(screen.queryByTestId("zoom-indicator")).toBeNull();
  });

  it("stays while the pointer rests on it", () => {
    render(<ZoomIndicator />);
    act(() => showZoomIndicator());
    fireEvent.pointerEnter(screen.getByTestId("zoom-indicator"));
    act(() => vi.advanceTimersByTime(ZOOM_INDICATOR_MS * 3));
    expect(screen.getByTestId("zoom-indicator")).toBeTruthy();

    fireEvent.pointerLeave(screen.getByTestId("zoom-indicator"));
    act(() => vi.advanceTimersByTime(ZOOM_INDICATOR_MS + 10));
    expect(screen.queryByTestId("zoom-indicator")).toBeNull();
  });
});
