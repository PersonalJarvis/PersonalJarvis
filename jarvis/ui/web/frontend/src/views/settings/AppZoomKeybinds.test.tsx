/**
 * Settings → Keyboard shortcuts: the zoom chords are recorded by character, so
 * Plus and Minus can be set on any keyboard layout.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { AppZoomKeybinds } from "./AppZoomKeybinds";
import { useAppZoomSupport } from "@/hooks/useAppZoom";
import { defaultAppZoomBindings } from "@/lib/appZoom";
import {
  APP_ZOOM_STORAGE_KEY,
  readAppZoomSettings,
  useAppZoomSettings,
} from "@/store/appZoomSettings";
import {
  QUICK_SWITCH_STORAGE_KEY,
  readQuickSwitchSettings,
  useQuickSwitchSettings,
} from "@/store/quickSwitchSettings";

describe("AppZoomKeybinds", () => {
  beforeEach(() => {
    window.localStorage.removeItem(APP_ZOOM_STORAGE_KEY);
    window.localStorage.removeItem(QUICK_SWITCH_STORAGE_KEY);
    useAppZoomSettings.setState(readAppZoomSettings());
    useQuickSwitchSettings.setState(readQuickSwitchSettings());
    useAppZoomSupport.setState({ support: "native" });
  });
  afterEach(cleanup);

  it("records a new zoom-in chord and remembers it", () => {
    render(<AppZoomKeybinds />);
    fireEvent.click(screen.getByTestId("app-zoom-record-in"));
    expect(screen.getByTestId("app-zoom-row-in").getAttribute("data-keybind-recording")).toBe("true");

    fireEvent.keyDown(window, { key: "ArrowUp", code: "ArrowUp", altKey: true });
    expect(useAppZoomSettings.getState().bindings.in).toBe("alt+up");
    expect(readAppZoomSettings().bindings.in).toBe("alt+up");
    expect(screen.getByTestId("app-zoom-row-in").hasAttribute("data-keybind-recording")).toBe(false);
  });

  it("records Plus on a German keyboard without the layout's key position", () => {
    useAppZoomSettings.getState().setBinding("in", "f9");
    render(<AppZoomKeybinds />);
    fireEvent.click(screen.getByTestId("app-zoom-record-in"));
    fireEvent.keyDown(window, { key: "+", code: "BracketRight", ctrlKey: true });
    expect(useAppZoomSettings.getState().bindings.in).toBe("ctrl+plus");
  });

  it("refuses a chord another step uses, and keeps the old one", () => {
    render(<AppZoomKeybinds />);
    const before = useAppZoomSettings.getState().bindings.in;
    fireEvent.click(screen.getByTestId("app-zoom-record-in"));
    fireEvent.keyDown(window, { key: "-", code: "Minus", ctrlKey: true });
    expect(useAppZoomSettings.getState().bindings.in).toBe(before);
    expect(screen.getByRole("alert").textContent).toMatch(/zoom step/i);
  });

  it("refuses the quick switcher's chord", () => {
    render(<AppZoomKeybinds />);
    fireEvent.click(screen.getByTestId("app-zoom-record-reset"));
    fireEvent.keyDown(window, { key: " ", code: "Space", ctrlKey: true });
    expect(screen.getByRole("alert").textContent).toMatch(/quick switcher/i);
  });

  it("cancels on Escape and resets to the default", () => {
    useAppZoomSettings.getState().setBinding("out", "alt+down");
    render(<AppZoomKeybinds />);
    fireEvent.click(screen.getByTestId("app-zoom-record-out"));
    fireEvent.keyDown(window, { key: "Escape", code: "Escape" });
    expect(useAppZoomSettings.getState().bindings.out).toBe("alt+down");

    fireEvent.click(screen.getByText("Reset to default"));
    expect(useAppZoomSettings.getState().bindings.out).toBe(defaultAppZoomBindings().out);
  });

  it("steps the size with its buttons and resets on the percentage", () => {
    render(<AppZoomKeybinds />);
    fireEvent.click(screen.getByTestId("app-zoom-step-in"));
    expect(screen.getByTestId("app-zoom-level").textContent).toBe("110 %");
    fireEvent.click(screen.getByTestId("app-zoom-level"));
    expect(useAppZoomSettings.getState().level).toBe(1);
  });

  it("explains that a browser tab keeps its own zoom", () => {
    useAppZoomSupport.setState({ support: "browser" });
    render(<AppZoomKeybinds />);
    expect(screen.queryByTestId("app-zoom-level")).toBeNull();
    expect(screen.getByTestId("app-zoom-note").textContent).toMatch(/browser/i);
  });
});
