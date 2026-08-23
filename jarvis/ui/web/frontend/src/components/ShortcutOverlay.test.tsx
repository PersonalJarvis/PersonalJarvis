/**
 * The overlay must show the binding the machine has, not the one it shipped
 * with. That is the difference between a useful list and a misleading one, so
 * the rebind case is the test that matters most here.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { ShortcutOverlay } from "./ShortcutOverlay";

const keybindsState = {
  config: null as null | {
    keybinds: Record<string, string>;
    defaults: Record<string, string>;
    suggestions: string[];
    restart_required: boolean;
  },
  loading: false,
  error: null,
  refetch: vi.fn(),
  saveKeybind: vi.fn(),
};

vi.mock("@/hooks/useHotkey", () => ({
  useKeybinds: () => keybindsState,
}));

vi.mock("@/views/settings/keyboardLayout", () => ({
  detectKeyboardPlatform: () => "pc",
}));

vi.mock("@/i18n", () => ({
  // Render the key itself so assertions read against a stable string.
  useT: () => (key: string) => key,
}));

beforeEach(() => {
  keybindsState.config = {
    keybinds: {},
    defaults: {},
    suggestions: [],
    restart_required: false,
  };
});

function open() {
  return render(<ShortcutOverlay open onOpenChange={() => {}} />);
}

/** The caps rendered on one shortcut's row, found by its label. */
function capsForRow(labelKey: string): string[] {
  const row = screen.getByText(labelKey).closest("li");
  if (!row) throw new Error(`no row for ${labelKey}`);
  return [...row.querySelectorAll("kbd")].map((k) => k.textContent ?? "");
}

describe("ShortcutOverlay", () => {
  it("renders both areas with their entries", () => {
    open();
    expect(screen.getByTestId("shortcut-overlay")).toBeTruthy();
    expect(screen.getByText("shortcut_overlay.area.voice")).toBeTruthy();
    expect(screen.getByText("shortcut_overlay.area.workspace")).toBeTruthy();
    expect(screen.getByText("shortcut_overlay.voice.dictate")).toBeTruthy();
  });

  it("shows the CURRENT binding after a rebind, not the shipped default", () => {
    keybindsState.config = {
      keybinds: { dictate: "ctrl+shift+j" },
      // The default is deliberately different: if the overlay ever read this
      // field, the assertion below would catch it.
      defaults: { dictate: "f9" },
      suggestions: [],
      restart_required: false,
    };
    open();
    expect(capsForRow("shortcut_overlay.voice.dictate")).toEqual([
      "Ctrl",
      "Shift",
      "J",
    ]);
    expect(screen.queryByText("F9")).toBeNull();
  });

  it("says a voice key is unassigned rather than showing a chord it does not have", () => {
    open();
    expect(screen.getAllByText("shortcut_overlay.unassigned").length).toBeGreaterThan(0);
    expect(capsForRow("shortcut_overlay.voice.call")).toEqual([]);
  });

  it("draws the platform modifier for this keyboard", () => {
    open();
    // detectKeyboardPlatform is mocked to "pc", so Mod must render as Ctrl.
    expect(capsForRow("shortcut_overlay.workspace.zoom_reset")).toEqual([
      "Ctrl",
      "0",
    ]);
    expect(screen.queryByText("⌘")).toBeNull();
  });

  it("renders nothing while closed", () => {
    render(<ShortcutOverlay open={false} onOpenChange={() => {}} />);
    expect(screen.queryByTestId("shortcut-overlay")).toBeNull();
  });
});
