import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installPasteBridge, isPasteChord } from "./terminalPaste";

const DELAY = 140;

/** Stand-in for the xterm terminal, recording what the bridge does to it. */
function fakeTerminal(): {
  attachCustomKeyEventHandler: (h: (e: KeyboardEvent) => boolean) => void;
  paste: (text: string) => void;
  press: (init: KeyboardEventInit) => boolean;
  pasted: string[];
} {
  let handler: (event: KeyboardEvent) => boolean = () => true;
  const pasted: string[] = [];
  return {
    attachCustomKeyEventHandler: (h) => {
      handler = h;
    },
    paste: (text: string) => {
      pasted.push(text);
    },
    press: (init: KeyboardEventInit) =>
      handler(new KeyboardEvent("keydown", init)),
    pasted,
  };
}

/** jsdom has no ClipboardEvent — hand-build one with the parts we read. */
function firePaste(
  container: HTMLElement,
  payload: { text?: string; file?: boolean },
): void {
  const event = new Event("paste", { bubbles: true });
  Object.defineProperty(event, "clipboardData", {
    value: {
      getData: () => payload.text ?? "",
      items: payload.file ? [{ kind: "file" }] : [],
    },
  });
  container.dispatchEvent(event);
}

describe("recognising the paste chord", () => {
  it("takes Ctrl+V away from Windows and Linux", () => {
    const event = new KeyboardEvent("keydown", { key: "v", ctrlKey: true });
    expect(isPasteChord(event, false)).toBe(true);
  });

  it("takes Cmd+V on a Mac, and leaves Ctrl+V there alone", () => {
    const cmd = new KeyboardEvent("keydown", { key: "v", metaKey: true });
    const ctrl = new KeyboardEvent("keydown", { key: "v", ctrlKey: true });
    expect(isPasteChord(cmd, true)).toBe(true);
    expect(isPasteChord(ctrl, true)).toBe(false);
  });

  it("also takes the traditional terminal binding Ctrl+Shift+V", () => {
    const event = new KeyboardEvent("keydown", {
      key: "V",
      ctrlKey: true,
      shiftKey: true,
    });
    expect(isPasteChord(event, false)).toBe(true);
  });

  it("leaves AltGr combinations alone — they type real characters", () => {
    const event = new KeyboardEvent("keydown", {
      key: "v",
      ctrlKey: true,
      altKey: true,
    });
    expect(isPasteChord(event, false)).toBe(false);
  });

  it("leaves a plain v and other control codes alone", () => {
    expect(isPasteChord(new KeyboardEvent("keydown", { key: "v" }), false)).toBe(
      false,
    );
    expect(
      isPasteChord(
        new KeyboardEvent("keydown", { key: "c", ctrlKey: true }),
        false,
      ),
    ).toBe(false);
  });
});

describe("pasting into a terminal pane", () => {
  let container: HTMLDivElement;
  let dispose: () => void;

  beforeEach(() => {
    vi.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
  });

  afterEach(() => {
    dispose?.();
    container.remove();
    vi.useRealTimers();
  });

  /**
   * The regression guard for the reported bug: xterm turns Ctrl+V into the
   * control code ^V and cancels the event, which stops the browser from ever
   * pasting. Returning false is what keeps xterm out of it.
   */
  it("takes the chord away from xterm, and nothing else", () => {
    const term = fakeTerminal();
    dispose = installPasteBridge(term, container, {
      readClipboard: async () => "",
      isMac: false,
    });

    expect(term.press({ key: "v", ctrlKey: true })).toBe(false);
    expect(term.press({ key: "c", ctrlKey: true })).toBe(true);
    expect(term.press({ key: "a" })).toBe(true);
  });

  it("pastes the operating system's clipboard when the browser stays silent", async () => {
    const term = fakeTerminal();
    dispose = installPasteBridge(term, container, {
      readClipboard: async () => "npm run build",
      isMac: false,
      fallbackDelayMs: DELAY,
    });

    term.press({ key: "v", ctrlKey: true });
    expect(term.pasted).toEqual([]); // still giving the browser its chance
    await vi.advanceTimersByTimeAsync(DELAY);

    expect(term.pasted).toEqual(["npm run build"]);
  });

  it("does not paste twice when the browser did deliver the text", async () => {
    const term = fakeTerminal();
    dispose = installPasteBridge(term, container, {
      readClipboard: async () => "npm run build",
      isMac: false,
      fallbackDelayMs: DELAY,
    });

    term.press({ key: "v", ctrlKey: true });
    firePaste(container, { text: "npm run build" });
    await vi.advanceTimersByTimeAsync(DELAY);

    expect(term.pasted).toEqual([]);
  });

  it("keeps out of the way when the clipboard carried a screenshot", async () => {
    const term = fakeTerminal();
    dispose = installPasteBridge(term, container, {
      readClipboard: async () => "some unrelated text",
      isMac: false,
      fallbackDelayMs: DELAY,
    });

    term.press({ key: "v", ctrlKey: true });
    firePaste(container, { file: true });
    await vi.advanceTimersByTimeAsync(DELAY);

    expect(term.pasted).toEqual([]);
  });

  /**
   * How every dictation tool inserts text: put it on the clipboard, send
   * Ctrl+V, then put the user's previous clipboard back. Reading the clipboard
   * only after the wait would sometimes land after that restore and paste the
   * text the user had copied earlier instead of what they just said.
   */
  it("pastes what was dictated, not what the tool restored afterwards", async () => {
    const term = fakeTerminal();
    let clipboard = "the dictated sentence";
    dispose = installPasteBridge(term, container, {
      readClipboard: async () => clipboard,
      isMac: false,
      fallbackDelayMs: DELAY,
    });

    term.press({ key: "v", ctrlKey: true });
    clipboard = "whatever the user had copied before"; // the tool puts it back
    await vi.advanceTimersByTimeAsync(DELAY);

    expect(term.pasted).toEqual(["the dictated sentence"]);
  });

  it("says so when no path could read the clipboard", async () => {
    const term = fakeTerminal();
    const onUnavailable = vi.fn();
    dispose = installPasteBridge(term, container, {
      readClipboard: async () => null,
      isMac: false,
      fallbackDelayMs: DELAY,
      onUnavailable,
    });

    term.press({ key: "v", ctrlKey: true });
    await vi.advanceTimersByTimeAsync(DELAY);

    expect(onUnavailable).toHaveBeenCalledOnce();
    expect(term.pasted).toEqual([]);
  });

  it("treats an empty clipboard as nothing to do, not as a failure", async () => {
    const term = fakeTerminal();
    const onUnavailable = vi.fn();
    dispose = installPasteBridge(term, container, {
      readClipboard: async () => "",
      isMac: false,
      fallbackDelayMs: DELAY,
      onUnavailable,
    });

    term.press({ key: "v", ctrlKey: true });
    await vi.advanceTimersByTimeAsync(DELAY);

    expect(onUnavailable).not.toHaveBeenCalled();
    expect(term.pasted).toEqual([]);
  });

  it("stops reading the clipboard once the pane is gone", async () => {
    const term = fakeTerminal();
    dispose = installPasteBridge(term, container, {
      readClipboard: async () => "late",
      isMac: false,
      fallbackDelayMs: DELAY,
    });

    term.press({ key: "v", ctrlKey: true });
    dispose();
    await vi.advanceTimersByTimeAsync(DELAY);

    expect(term.pasted).toEqual([]);
  });
});
