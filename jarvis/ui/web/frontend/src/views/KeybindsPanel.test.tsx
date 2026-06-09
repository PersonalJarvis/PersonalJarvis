import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  it("captures a two-key chord (F7 + F8) pressed simultaneously", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => FULL }),
    );
    render(<KeybindsPanel />);

    // Start recording on the Call row by clicking its current-combo field.
    const callField = await waitFor(() => screen.getByText("F3 + F4"));
    fireEvent.click(callField);

    // Press F7 and F8 together (overlapping), then release — the recorder must
    // keep BOTH, not abort on the first key like the old single-key capture.
    fireEvent.keyDown(window, { code: "F7", key: "F7" });
    fireEvent.keyDown(window, { code: "F8", key: "F8" });
    fireEvent.keyUp(window, { code: "F8", key: "F8" });

    await waitFor(() => expect(screen.getByText("F7 + F8")).toBeTruthy());
  });

  it("captures a chord via the Record button regardless of focus", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => FULL }),
    );
    render(<KeybindsPanel />);

    await waitFor(() => screen.getByText("F3 + F4"));
    // Clicking "Record" must arm capture even though focus lands on that button
    // (the old bug: the key listener only lived on the display field).
    const recordButtons = screen.getAllByRole("button", { name: /record/i });
    fireEvent.click(recordButtons[0]);

    fireEvent.keyDown(window, { code: "KeyI", key: "i" });
    fireEvent.keyDown(window, { code: "KeyY", key: "y" });
    fireEvent.keyUp(window, { code: "KeyY", key: "y" });

    await waitFor(() => expect(screen.getByText("I + Y")).toBeTruthy());
  });
});
