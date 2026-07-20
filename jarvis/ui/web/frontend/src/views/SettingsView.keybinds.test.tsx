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
  const defaultConfig = {
    keybinds: { call: "f3+f4", hangup: "f1+f2" },
    defaults: { call: "f3+f4", hangup: "f1+f2" },
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
