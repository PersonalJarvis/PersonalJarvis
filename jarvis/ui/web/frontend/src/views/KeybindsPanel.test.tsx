import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KeybindsPanel } from "./SettingsView";
// Resolved through the real i18n layer, not hardcoded: the locale strings are
// owned by another surface, so the assertion pins the KEY the component uses
// rather than a copy of the English wording.
import { translate } from "@/i18n";

// All four keybind actions, as the backend reports them (KEYBIND_ACTIONS in
// jarvis/core/config_writer.py). Settings renders three of them — hands-free
// dictation lives in the voice section's Shortcuts tab — but the panel must
// stay correct when the config carries an action it does not render.
const FULL = {
  keybinds: {
    call: "f3+f4",
    hangup: "f1+f2",
    dictate: "ctrl+right_alt+j",
    dictate_toggle: "ctrl+right_alt+space",
  },
  defaults: {
    call: "f3+f4",
    hangup: "f1+f2",
    dictate: "ctrl+right_alt+j",
    dictate_toggle: "ctrl+right_alt+space",
  },
  suggestions: [],
  restart_required: true,
};

afterEach(() => vi.restoreAllMocks());

/** One recorded request, so a test can assert what actually went out. */
interface RecordedCall {
  url: string;
  method: string;
  body?: unknown;
}

/** Stub the API and return the list the calls land in (empty until they do). */
function stubFetch(): RecordedCall[] {
  const calls: RecordedCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown, init?: { method?: string; body?: unknown }) => {
      calls.push({
        url: String(url),
        method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      return { ok: true, json: async () => FULL };
    }),
  );
  return calls;
}

/** The combo field's visible text with whitespace collapsed ("F3+F4") —
 * works for both the plain string and the kbd-chip rendering. */
function comboText(action: "call" | "hangup" | "dictate"): string {
  return (
    screen
      .getByTestId(`combo-field-${action}`)
      .textContent?.replace(/\s+/g, "") ?? ""
  );
}

describe("KeybindsPanel", () => {
  it("renders only Call and Hangup rows with their current combos", async () => {
    stubFetch();
    render(<KeybindsPanel />);
    // The two supported current combos render (formatted by formatCombo).
    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    expect(comboText("hangup")).toBe("F1+F2");
    expect(screen.queryByTestId("combo-field-ptt")).toBeNull();
    expect(screen.queryByText(/push-to-talk/i)).toBeNull();
  });

  it("never duplicates a dictation row that belongs to the voice section", async () => {
    stubFetch();
    render(<KeybindsPanel />);
    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    // Hands-free dictation is a real action, but its row lives in the voice
    // section's Shortcuts tab — never duplicated here. Push-to-talk is moving
    // the same way (Settings keeps Call and Hangup); while it is still rendered
    // here it must at least name the right Alt key the way its keycap reads,
    // not "Right-Alt".
    expect(screen.queryByTestId("combo-field-dictate_toggle")).toBeNull();
    if (screen.queryByTestId("combo-field-dictate")) {
      expect(comboText("dictate")).toBe("Ctrl+AltGr+J");
    }
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

  it("gives the user unlimited time to think while modifiers are held", async () => {
    // The reported bug: "I press Ctrl+Alt, and before I can decide what the
    // third key is, it has already saved Ctrl+Alt." Holding modifiers is how a
    // human builds a chord, so the recorder must wait — not for longer, but for
    // as long as the keys are down.
    const calls = stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));

    vi.useFakeTimers();
    try {
      fireEvent.keyDown(window, {
        code: "ControlLeft",
        key: "Control",
        ctrlKey: true,
      });
      fireEvent.keyDown(window, {
        code: "AltLeft",
        key: "Alt",
        ctrlKey: true,
        altKey: true,
      });
      // Half a minute of deciding. Nothing may leave for the backend.
      act(() => {
        vi.advanceTimersByTime(30_000);
      });
      expect(calls.some((c) => c.method === "PUT")).toBe(false);
      // The third key still lands in the SAME gesture.
      fireEvent.keyDown(window, {
        code: "KeyX",
        key: "x",
        ctrlKey: true,
        altKey: true,
      });
    } finally {
      vi.useRealTimers();
    }

    fireEvent.keyUp(window, { code: "KeyX", key: "x", ctrlKey: true, altKey: true });
    fireEvent.keyUp(window, { code: "AltLeft", key: "Alt", ctrlKey: true });
    fireEvent.keyUp(window, { code: "ControlLeft", key: "Control" });

    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.method === "PUT" &&
            (c.body as { hotkey: string }).hotkey === "ctrl+alt+x",
        ),
      ).toBe(true),
    );
  });

  it("treats a key's auto-repeat as proof it is still held", async () => {
    // A held ordinary key reports itself as repeated keydowns. Each one has to
    // push the swallowed-keyup rescue out of reach, or holding one key while
    // reaching for the next commits the half-built chord.
    const calls = stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));

    vi.useFakeTimers();
    try {
      fireEvent.keyDown(window, { code: "KeyX", key: "x" });
      for (let i = 0; i < 10; i++) {
        act(() => {
          vi.advanceTimersByTime(500);
        });
        fireEvent.keyDown(window, { code: "KeyX", key: "x", repeat: true });
      }
      expect(calls.some((c) => c.method === "PUT")).toBe(false);
    } finally {
      vi.useRealTimers();
    }

    fireEvent.keyUp(window, { code: "KeyX", key: "x" });
    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.method === "PUT" && (c.body as { hotkey: string }).hotkey === "x",
        ),
      ).toBe(true),
    );
  });

  it("ends the gesture when the window loses focus with keys still down", async () => {
    // Pressing the Windows key opens Start and takes focus with it, so its
    // keyup is delivered somewhere else and never arrives. Waiting for a
    // release that cannot come would leave the recorder armed forever.
    const calls = stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));

    fireEvent.keyDown(window, {
      code: "ControlLeft",
      key: "Control",
      ctrlKey: true,
    });
    fireEvent.keyDown(window, {
      code: "MetaLeft",
      key: "Meta",
      ctrlKey: true,
      metaKey: true,
    });
    fireEvent.blur(window);

    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.method === "PUT" &&
            (c.body as { hotkey: string }).hotkey === "ctrl+win",
        ),
      ).toBe(true),
    );
  });

  it("recovers from a swallowed modifier keyup on the next mouse move", async () => {
    // A mouse event carries the TRUE modifier state, so it is a free, continuous
    // repair for a phantom held modifier — the one state no timer may resolve.
    const calls = stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));

    fireEvent.keyDown(window, {
      code: "ControlLeft",
      key: "Control",
      ctrlKey: true,
    });
    fireEvent.keyDown(window, { code: "KeyJ", key: "j", ctrlKey: true });
    fireEvent.keyUp(window, { code: "KeyJ", key: "j", ctrlKey: true });
    // Ctrl's keyup never arrives — but the mouse says it is up.
    fireEvent.mouseMove(window, { ctrlKey: false });

    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.method === "PUT" && (c.body as { hotkey: string }).hotkey === "ctrl+j",
        ),
      ).toBe(true),
    );
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

  it("saves a recorded chord the moment the keys are released", async () => {
    // The reported annoyance: "I have to press a Save button first." A combo
    // is not a form field — letting go of the keys IS the decision, so there
    // is nothing left for a button to confirm.
    const calls = stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));

    fireEvent.keyDown(window, { code: "F7", key: "F7" });
    fireEvent.keyDown(window, { code: "F8", key: "F8" });
    fireEvent.keyUp(window, { code: "F8", key: "F8" });
    fireEvent.keyUp(window, { code: "F7", key: "F7" });

    await waitFor(() => {
      const put = calls.find((c) => c.method === "PUT");
      expect(put?.url).toBe("/api/settings/keybinds");
      expect(put?.body).toMatchObject({ action: "call", hotkey: "f7+f8" });
    });
    // No settle delay on a physical chord — nothing follows a full release.
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1);
  });

  it("does not save anything when Esc cancels the recording", async () => {
    const calls = stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));
    fireEvent.keyDown(window, { code: "F7", key: "F7" });
    fireEvent.keyDown(window, { code: "Escape", key: "Escape" });

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    // Auto-save refuses anything the server already has, which is what makes
    // Esc, the initial load and a refetch safe without any of them knowing.
    await new Promise((r) => setTimeout(r, 1500));
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("still saves on a live overlap and explains it instead", async () => {
    const calls = stubFetch();
    render(<KeybindsPanel />);

    const recordButtons = await waitFor(() =>
      screen.getAllByRole("button", { name: /record/i }),
    );
    fireEvent.click(recordButtons[0]); // Call row (f3+f4)

    // Strip the current combo, then pick F1 — a subset of hangup's F1+F2.
    fireEvent.click(screen.getByTestId("key-F3"));
    fireEvent.click(screen.getByTestId("key-F4"));
    fireEvent.click(screen.getByTestId("key-F1"));

    // An overlap is a caution now, not a wall: blocking it made a
    // modifier-only chord unsavable, since one is a subset of nearly every
    // other shortcut. The user is told and decides.
    const line = await waitFor(() =>
      screen.getByTestId("keybind-validation-call"),
    );
    expect(line.textContent).toBeTruthy();
    await waitFor(
      () =>
        expect(
          calls.some(
            (c) => c.method === "PUT" && (c.body as { hotkey: string }).hotkey === "f1",
          ),
        ).toBe(true),
      { timeout: 4000 },
    );
  });

  it("saves a modifier-only combo (Ctrl+Win) and cautions instead of blocking", async () => {
    const calls = stubFetch();
    render(<KeybindsPanel />);

    const recordButtons = await waitFor(() =>
      screen.getAllByRole("button", { name: /record/i }),
    );
    fireEvent.click(recordButtons[0]); // Call row (f3+f4)

    fireEvent.click(screen.getByTestId("key-F3"));
    fireEvent.click(screen.getByTestId("key-F4"));
    fireEvent.click(screen.getByTestId("key-ControlLeft"));
    fireEvent.click(screen.getByTestId("key-MetaLeft"));

    // The clicked modifiers stay visibly selected (the old composeCombo
    // collapsed a modifier-only state to "" and silently dropped it) …
    expect(
      screen.getByTestId("key-ControlLeft").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByTestId("key-MetaLeft").getAttribute("aria-pressed"),
    ).toBe("true");
    // … and the combo is a real, saveable one. Inverted from the old "blocks
    // saving until a real key lands": the user owns their keyboard, so the
    // prefix behaviour is a sentence, not a wall.
    expect(comboText("call")).toBe("Ctrl+Win");
    expect(screen.getByTestId("keybind-validation-call")).toBeTruthy();
    await waitFor(
      () =>
        expect(
          calls.some(
            (c) =>
              c.method === "PUT" &&
              (c.body as { hotkey: string }).hotkey === "ctrl+win",
          ),
        ).toBe(true),
      { timeout: 4000 },
    );
  });

  it("records Ctrl+Win from a physical chord (the gesture used to vanish)", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));

    // Holding two modifiers used to record literally nothing — chordToCombo
    // returned null with no key in the held set, so the field stayed blank and
    // the gesture expired silently.
    fireEvent.keyDown(window, { code: "ControlLeft", key: "Control", ctrlKey: true });
    fireEvent.keyDown(window, {
      code: "MetaLeft",
      key: "Meta",
      ctrlKey: true,
      metaKey: true,
    });
    fireEvent.keyUp(window, { code: "MetaLeft", key: "Meta", ctrlKey: true });
    fireEvent.keyUp(window, { code: "ControlLeft", key: "Control" });

    await waitFor(() => expect(comboText("call")).toBe("Ctrl+Win"));
  });

  it("records a mouse side button without the press dismissing the recorder", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));

    // Button 3 is X1 ("Back"). A captured press must not reach the page as an
    // ordinary click, or it would navigate the app away mid-recording.
    const down = fireEvent.mouseDown(window, { button: 3 });
    expect(down).toBe(false); // preventDefault() was called
    fireEvent.mouseUp(window, { button: 3 });

    await waitFor(() => expect(comboText("call")).toBe("MouseBack"));
  });

  it("leaves the primary mouse button alone so the picker stays clickable", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));

    // Left-clicking anything while armed must behave normally — the Save
    // button and the on-screen keys are operated with it.
    expect(fireEvent.mouseDown(window, { button: 0 })).toBe(true);
    expect(comboText("call")).toBe("F3+F4");
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

  it("offers the Windows key for click-to-assign (it is not reserved)", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    const recordButtons = await waitFor(() =>
      screen.getAllByRole("button", { name: /record/i }),
    );
    fireEvent.click(recordButtons[0]);

    // Inverted: the cap used to be drawn dead with a "reserved by the system"
    // tooltip. Clicking it is the path that MATTERS for this key — pressing it
    // physically makes Windows open Start and pull focus out of the app
    // mid-recording, so the on-screen key has to work.
    const winKey = screen.getByTestId("key-MetaLeft") as HTMLButtonElement;
    expect(winKey.disabled).toBe(false);
    fireEvent.click(winKey);
    expect(winKey.getAttribute("aria-pressed")).toBe("true");
  });

  it("offers the bindable mouse buttons on the picker", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    const recordButtons = await waitFor(() =>
      screen.getAllByRole("button", { name: /record/i }),
    );
    fireEvent.click(recordButtons[0]); // Call row (f3+f4)

    fireEvent.click(screen.getByTestId("key-F3"));
    fireEvent.click(screen.getByTestId("key-F4"));
    fireEvent.click(screen.getByTestId("key-MouseButton1")); // middle button

    expect(comboText("call")).toBe("MiddleClick");
  });

  it("says what to do while the recorder is empty and armed", async () => {
    stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));
    // Strip the current combo so the field is in the bare recording state that
    // "looked ugly": it used to say only that a mode was on, which reads as a
    // broken field. It now names the gesture, and the line under it spells out
    // how the gesture ends.
    fireEvent.click(screen.getByTestId("key-F3"));
    fireEvent.click(screen.getByTestId("key-F4"));

    expect(screen.getByTestId("combo-field-call").textContent).toContain(
      translate("settings_view.keybinds.record_prompt"),
    );
    expect(screen.getByText(translate("settings_view.keybinds.record_prompt_hint")))
      .toBeTruthy();
  });

  it("click-to-assign saves once the clicking settles, then closes", async () => {
    const calls = stubFetch();
    render(<KeybindsPanel />);

    await waitFor(() => expect(comboText("call")).toBe("F3+F4"));
    fireEvent.click(screen.getByTestId("combo-field-call"));
    expect(screen.getByTestId("key-F5")).toBeTruthy(); // keyboard open

    // Build the combo by CLICKING: F3 off, F4 off, F5 on. Each click is
    // probably not the last, so nothing is sent while they are still coming —
    // otherwise the half-built combo ("F4" alone here) would be saved first.
    fireEvent.click(screen.getByTestId("key-F3"));
    fireEvent.click(screen.getByTestId("key-F4"));
    fireEvent.click(screen.getByTestId("key-F5"));

    await waitFor(
      () => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1),
      { timeout: 4000 },
    );
    expect((calls.find((c) => c.method === "PUT")!.body as { hotkey: string }).hotkey)
      .toBe("f5");

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

  it("allows a solo navigation key with a warning and saves it anyway", async () => {
    const calls = stubFetch();
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
    await waitFor(
      () =>
        expect(
          calls.some(
            (c) => c.method === "PUT" && (c.body as { hotkey: string }).hotkey === "up",
          ),
        ).toBe(true),
      { timeout: 4000 },
    );
  });
});
