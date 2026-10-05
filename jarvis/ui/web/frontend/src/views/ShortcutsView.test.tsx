/**
 * The Keyboard shortcuts page: one list of every shortcut, and the key tester
 * telling what a pressed combination does — through the real matchers.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ShortcutsView } from "./ShortcutsView";
import { useEventStore } from "@/store/events";
import {
  QUICK_SWITCH_STORAGE_KEY,
  readQuickSwitchSettings,
  useQuickSwitchSettings,
} from "@/store/quickSwitchSettings";

const KEYBINDS = {
  keybinds: {
    call: "f3+f4",
    hangup: "f5",
    dictate: "ctrl+right_alt+j",
    dictate_toggle: "ctrl+right_alt+space",
    paste_last: "ctrl+alt+v",
  },
  defaults: {},
  suggestions: [],
  restart_required: false,
};

function press(init: KeyboardEventInit) {
  act(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, ...init }));
  });
}

describe("ShortcutsView", () => {
  beforeEach(() => {
    window.localStorage.removeItem(QUICK_SWITCH_STORAGE_KEY);
    useQuickSwitchSettings.setState({ ...readQuickSwitchSettings(), combo: "ctrl+space" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(KEYBINDS), { status: 200 })),
    );
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("lists every shortcut in one list, global keys first", async () => {
    render(<ShortcutsView />);
    const list = screen.getByTestId("shortcuts-list");
    const rows = list.querySelectorAll("li");
    // 14 registry entries + the three appshot keys.
    expect(rows.length).toBe(17);
    expect(screen.getByTestId("shortcuts-count").textContent).toContain("17");
    expect(rows[0].textContent).toContain("Anywhere on this computer");
    expect(rows[rows.length - 1].textContent).toContain("In an agent terminal");
    for (const id of ["call", "hangup", "dictate", "quick_switch", "appshot-hotkey"]) {
      expect(screen.getByTestId(`shortcut-row-${id}`)).toBeTruthy();
    }
    expect(await screen.findAllByText("Ctrl")).toBeTruthy();
  });

  it("opens the recorder under a voice key row", async () => {
    render(<ShortcutsView />);
    await act(async () => {});
    expect(screen.queryByTestId("combo-field-call")).toBeNull();
    fireEvent.click(screen.getByTestId("shortcuts-edit-call"));
    expect(screen.getByTestId("combo-field-call")).toBeTruthy();
  });

  it("shows the quick switcher as off once it is switched off", () => {
    render(<ShortcutsView />);
    fireEvent.click(screen.getByTestId("quick-switch-enabled"));
    const row = screen.getByTestId("shortcut-row-quick_switch");
    expect(row.textContent).toMatch(/off/i);
    expect(screen.getByTestId("shortcuts-edit-quick_switch").hasAttribute("disabled")).toBe(true);
  });

  it("names what the quick switcher chord does", () => {
    render(<ShortcutsView />);
    press({ key: " ", code: "Space", ctrlKey: true });
    expect(screen.getByTestId("shortcut-tester-meaning").textContent).toContain("Quick switcher");
  });

  it("names a voice keybind and calls a free chord free", async () => {
    render(<ShortcutsView />);
    await screen.findByTestId("shortcut-row-dictate");
    // Let the keybinds fetch settle before pressing.
    await act(async () => {});
    press({ key: "v", code: "KeyV", ctrlKey: true, altKey: true });
    expect(screen.getByTestId("shortcut-tester-meaning").textContent).toMatch(/paste/i);
    press({ key: "g", code: "KeyG", ctrlKey: true, shiftKey: true });
    expect(screen.getByTestId("shortcut-tester-meaning").textContent).toMatch(/free/i);
  });

  it("ignores plain typing in a field but still reads a chord there", () => {
    render(
      <>
        <input data-testid="field" />
        <ShortcutsView />
      </>,
    );
    const field = screen.getByTestId("field");
    const before = screen.getByTestId("shortcut-tester-meaning").textContent;
    fireEvent.keyDown(field, { key: "g", code: "KeyG" });
    expect(screen.getByTestId("shortcut-tester-meaning").textContent).toBe(before);
    // The hub opens with its search box focused; a chord must still register.
    fireEvent.keyDown(field, { key: "g", code: "KeyG", ctrlKey: true, shiftKey: true });
    expect(screen.getByTestId("shortcut-tester-meaning").textContent).toMatch(/free/i);
  });

  it("sends the dictation edit to the voice section", () => {
    render(<ShortcutsView />);
    fireEvent.click(screen.getByTestId("shortcuts-edit-dictate"));
    expect(useEventStore.getState().activeSection).toBe("voice-shortcuts");
  });

  it("sends the appshot keys to the Appshots section", () => {
    render(<ShortcutsView />);
    fireEvent.click(screen.getByTestId("shortcuts-edit-appshot-region_hotkey"));
    expect(useEventStore.getState().activeSection).toBe("appshots");
  });
});
