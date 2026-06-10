import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: async () => FULL }),
  );
}

/** The combo field's visible text with whitespace collapsed ("F3+F4") —
 * works for both the plain string and the kbd-chip rendering. */
function comboText(action: "call" | "hangup" | "ptt"): string {
  return (
    screen
      .getByTestId(`combo-field-${action}`)
      .textContent?.replace(/\s+/g, "") ?? ""
  );
}

describe("KeybindsPanel", () => {
  it("renders one row per voice action with its current combo", async () => {
    stubFetch();
    render(<KeybindsPanel />);
    // The three current combos render (formatted by formatCombo).
    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    expect(comboText("hangup")).toBe("F1+F2");
  });

  it("captures a two-key chord (F7 + F8) pressed simultaneously", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    // Start recording on the Call row by clicking its current-combo field.
    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));

    // Press F7 and F8 together (overlapping), then release both — the recorder
    // must keep BOTH, not abort on the first key like the old single-key
    // capture, and only commits once every key is released.
    fireEvent.keyDown(window, { code: "F7", key: "F7" });
    fireEvent.keyDown(window, { code: "F8", key: "F8" });
    fireEvent.keyUp(window, { code: "F8", key: "F8" });
    fireEvent.keyUp(window, { code: "F7", key: "F7" });

    await waitFor(() => expect(comboText("call")).toBe("F7+F8"));
  });

  it("keeps every key when an early one lifts before the last is pressed (commits on full release)", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));

    // Roll across W → A → S → D, lifting W before S and D are even pressed —
    // the way a human actually "holds WASD". The recorder must keep
    // accumulating until EVERY key is released, not stop on the first keyup
    // (the old bug: pressing several keys only ever recorded the first one).
    fireEvent.keyDown(window, { code: "KeyW", key: "w" });
    fireEvent.keyDown(window, { code: "KeyA", key: "a" });
    fireEvent.keyUp(window, { code: "KeyW", key: "w" });
    fireEvent.keyDown(window, { code: "KeyS", key: "s" });
    fireEvent.keyDown(window, { code: "KeyD", key: "d" });
    fireEvent.keyUp(window, { code: "KeyA", key: "a" });
    fireEvent.keyUp(window, { code: "KeyS", key: "s" });
    fireEvent.keyUp(window, { code: "KeyD", key: "d" });

    await waitFor(() => expect(comboText("call")).toBe("A+D+S+W"));
  });

  it("commits a held function-key chord even when no keyup arrives (idle fallback)", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));

    // Function keys (F5/F6) sometimes never deliver a keyup to the WebView, so
    // the "commit on full release" path would hang forever. The recorder must
    // fall back to committing the held chord once the user stops pressing.
    vi.useFakeTimers();
    try {
      fireEvent.keyDown(window, { code: "F5", key: "F5" });
      fireEvent.keyDown(window, { code: "F6", key: "F6" });
      // No keyup at all — the releases were swallowed.
      act(() => {
        vi.advanceTimersByTime(1000);
      });
    } finally {
      vi.useRealTimers();
    }

    expect(comboText("call")).toBe("F5+F6");
  });

  it("lights up the on-screen keyboard live as keys are pressed", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    const recordButtons = await waitFor(() =>
      screen.getAllByRole("button", { name: /record/i }),
    );
    fireEvent.click(recordButtons[0]); // Call row → keyboard appears

    fireEvent.keyDown(window, { code: "F5", key: "F5" });
    fireEvent.keyDown(window, { code: "F6", key: "F6" });

    // The pressed style (inverted foreground) proves the live highlight works.
    expect(screen.getByTestId("key-F5").className).toContain(
      "text-primary-foreground",
    );
    expect(screen.getByTestId("key-F6").className).toContain(
      "text-primary-foreground",
    );

    // Stop → clears the pending idle-commit timer (no dangling timer).
    fireEvent.click(screen.getAllByRole("button", { name: /stop/i })[0]);
  });

  it("builds a combo by clicking keys on the on-screen keyboard", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    const recordButtons = await waitFor(() =>
      screen.getAllByRole("button", { name: /record/i }),
    );
    fireEvent.click(recordButtons[0]); // Call row starts as f3+f4

    // The starting combo's keys render as selected on the keyboard.
    expect(screen.getByTestId("key-F3").getAttribute("aria-pressed")).toBe("true");

    // Click F3 off and click J on — pure mouse, no physical key press.
    fireEvent.click(screen.getByTestId("key-F3"));
    fireEvent.click(screen.getByTestId("key-KeyJ"));

    // Stop → the field shows the clicked-together combo.
    fireEvent.click(screen.getAllByRole("button", { name: /stop/i })[0]);
    await waitFor(() => expect(comboText("call")).toBe("F4+J"));
  });

  it("captures a chord via the Record button regardless of focus", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    // Clicking "Record" must arm capture even though focus lands on that button
    // (the old bug: the key listener only lived on the display field).
    const recordButtons = screen.getAllByRole("button", { name: /record/i });
    fireEvent.click(recordButtons[0]);

    fireEvent.keyDown(window, { code: "KeyI", key: "i" });
    fireEvent.keyDown(window, { code: "KeyY", key: "y" });
    fireEvent.keyUp(window, { code: "KeyY", key: "y" });
    fireEvent.keyUp(window, { code: "KeyI", key: "i" });

    await waitFor(() => expect(comboText("call")).toBe("I+Y"));
  });

  it("disables Save and explains a live overlap with another action", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    const recordButtons = await waitFor(() =>
      screen.getAllByRole("button", { name: /record/i }),
    );
    fireEvent.click(recordButtons[0]); // Call row (f3+f4)

    // Strip the current combo, then pick F1 — a subset of hangup's F1+F2.
    fireEvent.click(screen.getByTestId("key-F3"));
    fireEvent.click(screen.getByTestId("key-F4"));
    fireEvent.click(screen.getByTestId("key-F1"));

    const line = await waitFor(() =>
      screen.getByTestId("keybind-validation-call"),
    );
    expect(line.textContent).toMatch(/hangup/i);
    const saveButtons = screen.getAllByRole("button", { name: /save/i });
    expect((saveButtons[0] as HTMLButtonElement).disabled).toBe(true);
  });

  it("keeps a clicked modifier visible and blocks saving until a real key lands", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    const recordButtons = await waitFor(() =>
      screen.getAllByRole("button", { name: /record/i }),
    );
    fireEvent.click(recordButtons[0]); // Call row (f3+f4)

    fireEvent.click(screen.getByTestId("key-F3"));
    fireEvent.click(screen.getByTestId("key-F4"));
    fireEvent.click(screen.getByTestId("key-ControlLeft"));

    // The clicked modifier stays visibly selected (the old composeCombo
    // collapsed a modifier-only state to "" and silently dropped it).
    expect(
      screen.getByTestId("key-ControlLeft").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByTestId("keybind-validation-call").textContent,
    ).toMatch(/real key/i);
    const saveButtons = screen.getAllByRole("button", { name: /save/i });
    expect((saveButtons[0] as HTMLButtonElement).disabled).toBe(true);
  });

  it("applies rapid-fire key toggles cumulatively (functional state update)", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    const recordButtons = await waitFor(() =>
      screen.getAllByRole("button", { name: /record/i }),
    );
    fireEvent.click(recordButtons[0]); // Call row (f3+f4)

    // Three toggles dispatched in the SAME task (no re-render in between) —
    // a closure-based setCombo makes each one start from the stale pre-click
    // combo, so only the last toggle survives ("F3" instead of "Q").
    act(() => {
      screen.getByTestId("key-KeyQ").click();
      screen.getByTestId("key-F3").click();
      screen.getByTestId("key-F4").click();
    });

    expect(comboText("call")).toBe("Q");
  });

  it("marks the Windows key as reserved (not clickable) on the keyboard", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    const recordButtons = await waitFor(() =>
      screen.getAllByRole("button", { name: /record/i }),
    );
    fireEvent.click(recordButtons[0]);

    const winKey = screen.getByTestId("key-MetaLeft") as HTMLButtonElement;
    expect(winKey.disabled).toBe(true);
  });

  it("closes the recorder after a successful save (no stale-snapshot Esc)", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));
    expect(screen.getByTestId("key-F5")).toBeTruthy(); // keyboard open

    // Build the combo by CLICKING (no physical keys → no auto-commit timer):
    // F3 off, F4 off, F5 on. The recorder stays open during click-to-assign.
    fireEvent.click(screen.getByTestId("key-F3"));
    fireEvent.click(screen.getByTestId("key-F4"));
    fireEvent.click(screen.getByTestId("key-F5"));
    const saveButtons = screen.getAllByRole("button", { name: /save/i });
    fireEvent.click(saveButtons[0]);

    // The save finishes the recording session — leaving it open kept a stale
    // pre-recording snapshot that a later Esc would "restore", silently
    // diverging the field from what the server actually has.
    await waitFor(() => expect(screen.queryByTestId("key-F5")).toBeNull());
  });

  it("restores the previous combo when Esc cancels a recording", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));

    // The live preview updates the field as keys land …
    fireEvent.keyDown(window, { code: "F7", key: "F7" });
    expect(comboText("call")).toBe("F7");

    // … but Esc must throw the half-built chord away, not keep the preview.
    fireEvent.keyDown(window, { code: "Escape", key: "Escape" });
    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
  });

  it("allows a solo navigation key with a warning, Save stays enabled", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    const recordButtons = await waitFor(() =>
      screen.getAllByRole("button", { name: /record/i }),
    );
    fireEvent.click(recordButtons[0]); // Call row (f3+f4)

    fireEvent.click(screen.getByTestId("key-F3"));
    fireEvent.click(screen.getByTestId("key-F4"));
    fireEvent.click(screen.getByTestId("key-ArrowUp"));

    // A warning line appears (fires during text navigation) but the combo is
    // legal — the user asked for Arrow Up, the user gets Arrow Up.
    expect(screen.getByTestId("keybind-validation-call")).toBeTruthy();
    const saveButtons = screen.getAllByRole("button", { name: /save/i });
    expect((saveButtons[0] as HTMLButtonElement).disabled).toBe(false);
  });
});
