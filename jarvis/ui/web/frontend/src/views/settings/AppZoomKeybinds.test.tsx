/**
 * Settings → Keyboard shortcuts: the zoom chords are recorded by character, so
 * Plus and Minus can be set on any keyboard layout.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { AppZoomChordRow, AppZoomKeybinds } from "./AppZoomKeybinds";
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

/** The page draws the switch and the three chord rows in two places; render both. */
function Zoom() {
  return (
    <>
      <AppZoomKeybinds />
      <ul>
        <AppZoomChordRow intent="in" title="Zoom in" />
        <AppZoomChordRow intent="out" title="Zoom out" />
        <AppZoomChordRow intent="reset" title="Back to 100 %" />
      </ul>
    </>
  );
}

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
    render(<Zoom />);
    fireEvent.click(screen.getByTestId("chord-record-app-zoom-in"));
    expect(screen.getByTestId("app-zoom-row-in").getAttribute("data-keybind-recording")).toBe("true");

    fireEvent.keyDown(window, { key: "ArrowUp", code: "ArrowUp", altKey: true });
    expect(useAppZoomSettings.getState().bindings.in).toBe("alt+up");
    expect(readAppZoomSettings().bindings.in).toBe("alt+up");
    expect(screen.getByTestId("app-zoom-row-in").hasAttribute("data-keybind-recording")).toBe(false);
  });

  it("records Plus on a German keyboard without the layout's key position", () => {
    useAppZoomSettings.getState().setBinding("in", "f9");
    render(<Zoom />);
    fireEvent.click(screen.getByTestId("chord-record-app-zoom-in"));
    fireEvent.keyDown(window, { key: "+", code: "BracketRight", ctrlKey: true });
    expect(useAppZoomSettings.getState().bindings.in).toBe("ctrl+plus");
  });

  it("refuses a chord another step uses, and keeps the old one", () => {
    render(<Zoom />);
    const before = useAppZoomSettings.getState().bindings.in;
    fireEvent.click(screen.getByTestId("chord-record-app-zoom-in"));
    fireEvent.keyDown(window, { key: "-", code: "Minus", ctrlKey: true });
    expect(useAppZoomSettings.getState().bindings.in).toBe(before);
    expect(screen.getByRole("alert").textContent).toMatch(/zoom step/i);
  });

  it("refuses the quick switcher's chord", () => {
    render(<Zoom />);
    fireEvent.click(screen.getByTestId("chord-record-app-zoom-reset"));
    fireEvent.keyDown(window, { key: " ", code: "Space", ctrlKey: true });
    expect(screen.getByRole("alert").textContent).toMatch(/quick switcher/i);
  });

  it("cancels on Escape and resets to the default", () => {
    useAppZoomSettings.getState().setBinding("out", "alt+down");
    render(<Zoom />);
    fireEvent.click(screen.getByTestId("chord-record-app-zoom-out"));
    fireEvent.keyDown(window, { key: "Escape", code: "Escape" });
    expect(useAppZoomSettings.getState().bindings.out).toBe("alt+down");

    // Remove and Reset live under the row while it records.
    fireEvent.click(screen.getByTestId("chord-record-app-zoom-out"));
    fireEvent.click(screen.getByText("Reset to default"));
    expect(useAppZoomSettings.getState().bindings.out).toBe(defaultAppZoomBindings().out);
  });

  it("removes a chord from the open recorder", () => {
    render(<Zoom />);
    fireEvent.click(screen.getByTestId("chord-record-app-zoom-in"));
    fireEvent.click(screen.getByTestId("chord-clear-app-zoom-in"));
    expect(useAppZoomSettings.getState().bindings.in).toBe("");
  });

  it("steps the size with its buttons and resets on the percentage", () => {
    render(<Zoom />);
    fireEvent.click(screen.getByTestId("app-zoom-step-in"));
    expect(screen.getByTestId("app-zoom-level").textContent).toBe("110 %");
    fireEvent.click(screen.getByTestId("app-zoom-level"));
    expect(useAppZoomSettings.getState().level).toBe(1);
  });

  it("explains that a browser tab keeps its own zoom", () => {
    useAppZoomSupport.setState({ support: "browser" });
    render(<Zoom />);
    expect(screen.queryByTestId("app-zoom-level")).toBeNull();
    expect(screen.getByTestId("app-zoom-note").textContent).toMatch(/browser/i);
  });
});
