import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KeybindsPanel } from "./SettingsView";

const FULL = {
  keybinds: { call: "f3+f4", hangup: "f1+f2", ptt: "ctrl+right_alt+j" },
  defaults: { call: "f3+f4", hangup: "f1+f2", ptt: "ctrl+right_alt+j" },
  push_to_talk: true,
  suggestions: [],
  restart_required: true,
};

afterEach(() => vi.restoreAllMocks());

describe("KeybindsPanel", () => {
  it("renders one row per voice action with its current combo", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => FULL }),
    );
    render(<KeybindsPanel />);
    // The three current combos render (formatted by formatCombo).
    await waitFor(() => expect(screen.getByText("F3 + F4")).toBeTruthy());
    expect(screen.getByText("F1 + F2")).toBeTruthy();
  });
});
