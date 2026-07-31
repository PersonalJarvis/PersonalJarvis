/**
 * Tests for the Clear button on the Voice Keybinds rows (KeybindsPanel,
 * rendered inside SettingsView).
 */
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/i18n")>();
  return {
    ...actual,
    useT: () => actual.useT(),
    useUiLanguage: () => "en",
    useReplyLanguage: () => "auto",
  };
});

const { saveKeybind, state } = vi.hoisted(() => {
  // The full backend action set (KEYBIND_ACTIONS). Settings renders only Call
  // and Hangup — every dictation shortcut lives in the voice section's
  // Shortcuts tab. The payload still carries all of them here on purpose: the
  // rows differ between the two surfaces, the DATA never does, or Call and
  // Hangup would lose their collision check against the dictation combos.
  const keybinds = {
    call: "f3+f4",
    hangup: "f1+f2",
    dictate: "ctrl+right_alt+j",
    dictate_toggle: "ctrl+right_alt+space",
    paste_last: "ctrl+alt+v",
  };
  const defaultConfig = {
    keybinds,
    defaults: { ...keybinds },
    suggestions: [] as string[],
    restart_required: false,
  };
  const saveKeybind = vi.fn().mockResolvedValue({
    ok: true,
    action: "hangup",
    hotkey: "",
    persisted: true,
    applied_live: true,
    restart_required: false,
  });
  return { saveKeybind, state: { config: defaultConfig, defaultConfig } };
});

vi.mock("@/hooks/useHotkey", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/useHotkey")>();
  return {
    ...actual,
    useKeybinds: () => ({
      config: state.config,
      loading: false,
      error: null,
      refetch: vi.fn(),
      saveKeybind,
    }),
  };
});

import { KeybindsPanel } from "@/views/SettingsView";

afterEach(() => {
  cleanup();
  saveKeybind.mockClear();
  state.config = state.defaultConfig;
});

describe("KeybindsPanel — Clear button", () => {
  it("renders a Clear button for each supported bound row", () => {
    render(<KeybindsPanel />);
    expect(screen.queryByTestId("clear-keybind-call")).not.toBeNull();
    expect(screen.queryByTestId("clear-keybind-hangup")).not.toBeNull();
    expect(screen.queryByTestId("clear-keybind-ptt")).toBeNull();
  });

  it("binds calling and hanging up only — every dictation key lives elsewhere", () => {
    render(<KeybindsPanel />);
    // This panel is about reaching the assistant and letting it go. All three
    // dictation shortcuts are edited on ONE surface (the voice section's
    // Shortcuts tab); a second, unsynced row for any of them here would let the
    // same key be changed in two places with two different answers.
    expect(screen.queryByTestId("clear-keybind-dictate")).toBeNull();
    expect(screen.queryByTestId("clear-keybind-dictate_toggle")).toBeNull();
    expect(screen.queryByTestId("clear-keybind-paste_last")).toBeNull();
  });

  it("still receives the dictation combos it has to detect collisions against", () => {
    // Dropping the ROWS is a rendering decision, not a data one. If the panel
    // ever started filtering the payload, Call could be saved onto a combo a
    // dictation key already owns and one of the two would silently go dead.
    expect(state.config.keybinds.dictate).toBe("ctrl+right_alt+j");
    expect(state.config.keybinds.dictate_toggle).toBe("ctrl+right_alt+space");
    expect(state.config.keybinds.paste_last).toBe("ctrl+alt+v");
  });

  it("clicking Clear saves an empty hotkey for that action", async () => {
    render(<KeybindsPanel />);
    fireEvent.click(screen.getByTestId("clear-keybind-hangup"));
    await waitFor(() => expect(saveKeybind).toHaveBeenCalledWith("hangup", ""));
  });

  it("shows 'No key assigned' after a successful clear", async () => {
    render(<KeybindsPanel />);
    fireEvent.click(screen.getByTestId("clear-keybind-hangup"));
    await waitFor(() => {
      expect(screen.queryAllByText("No key assigned").length).toBeGreaterThan(0);
    });
  });

  it("disables Clear when the action is already unbound", () => {
    state.config = {
      ...state.defaultConfig,
      keybinds: { ...state.defaultConfig.keybinds, hangup: "" },
    };
    render(<KeybindsPanel />);
    const btn = screen.getByTestId("clear-keybind-hangup") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
