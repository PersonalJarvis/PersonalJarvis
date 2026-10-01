/**
 * Settings → Keyboard shortcuts: the quick switcher can be switched off, and
 * its chord is stored per device.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QuickSwitchKeybind } from "./QuickSwitchKeybind";
import {
  QUICK_SWITCH_STORAGE_KEY,
  readQuickSwitchSettings,
  useQuickSwitchSettings,
} from "@/store/quickSwitchSettings";
import { defaultQuickSwitchCombo } from "@/lib/quickSwitchChord";

describe("QuickSwitchKeybind", () => {
  beforeEach(() => {
    window.localStorage.removeItem(QUICK_SWITCH_STORAGE_KEY);
    useQuickSwitchSettings.setState(readQuickSwitchSettings());
  });
  afterEach(cleanup);

  it("starts on, with the platform default", () => {
    render(<QuickSwitchKeybind voiceConfig={null} />);
    expect(useQuickSwitchSettings.getState().enabled).toBe(true);
    expect(useQuickSwitchSettings.getState().combo).toBe(defaultQuickSwitchCombo());
    expect(screen.getByTestId("combo-field-quick_switch")).toBeTruthy();
  });

  it("switches off, hides the recorder and remembers it", () => {
    render(<QuickSwitchKeybind voiceConfig={null} />);
    fireEvent.click(screen.getByTestId("quick-switch-enabled"));
    expect(useQuickSwitchSettings.getState().enabled).toBe(false);
    expect(screen.queryByTestId("combo-field-quick_switch")).toBeNull();
    expect(readQuickSwitchSettings().enabled).toBe(false);
  });

  it("keeps a stored chord across a reload", () => {
    useQuickSwitchSettings.getState().setCombo("Ctrl+Shift+K");
    expect(readQuickSwitchSettings().combo).toBe("ctrl+shift+k");
  });

  it("shows no overlap warning for a voice chord it merely contains", () => {
    const voice = {
      keybinds: { dictate_toggle: "ctrl+right_alt+space" },
      defaults: {},
      suggestions: [],
      restart_required: false,
    };
    useQuickSwitchSettings.getState().setCombo("ctrl+space");
    render(<QuickSwitchKeybind voiceConfig={voice} />);
    expect(screen.queryByTestId("keybind-validation-quick_switch")).toBeNull();
  });

  it("falls back to the defaults when storage holds garbage", () => {
    window.localStorage.setItem(QUICK_SWITCH_STORAGE_KEY, "{not json");
    expect(readQuickSwitchSettings()).toEqual({ enabled: true, combo: defaultQuickSwitchCombo() });
  });
});
