import { describe, expect, it } from "vitest";

import {
  NEWLINE_SEQUENCE,
  installNewlineBridge,
  isNewlineChord,
} from "./terminalNewline";

/** Stand-in for the xterm terminal, recording what the bridge does to it. */
function fakeTerminal(): {
  attachCustomKeyEventHandler: (h: (e: KeyboardEvent) => boolean) => void;
  input: (data: string) => void;
  press: (init: KeyboardEventInit, type?: string) => boolean;
  sent: string[];
} {
  let handler: (event: KeyboardEvent) => boolean = () => true;
  const sent: string[] = [];
  return {
    attachCustomKeyEventHandler: (h) => {
      handler = h;
    },
    input: (data: string) => {
      sent.push(data);
    },
    press: (init: KeyboardEventInit, type = "keydown") =>
      handler(new KeyboardEvent(type, { cancelable: true, ...init })),
    sent,
  };
}

describe("recognising the newline chord", () => {
  it("takes Shift+Enter — the reported bug", () => {
    expect(
      isNewlineChord(new KeyboardEvent("keydown", { key: "Enter", shiftKey: true })),
    ).toBe(true);
  });

  it("takes Option/Alt+Enter and Cmd+Enter", () => {
    expect(
      isNewlineChord(new KeyboardEvent("keydown", { key: "Enter", altKey: true })),
    ).toBe(true);
    expect(
      isNewlineChord(new KeyboardEvent("keydown", { key: "Enter", metaKey: true })),
    ).toBe(true);
  });

  it("leaves a plain Enter alone — that is still 'send'", () => {
    expect(isNewlineChord(new KeyboardEvent("keydown", { key: "Enter" }))).toBe(
      false,
    );
  });

  it("leaves Ctrl+Enter alone, and with it AltGr+Enter", () => {
    expect(
      isNewlineChord(new KeyboardEvent("keydown", { key: "Enter", ctrlKey: true })),
    ).toBe(false);
    expect(
      isNewlineChord(
        new KeyboardEvent("keydown", {
          key: "Enter",
          ctrlKey: true,
          altKey: true,
        }),
      ),
    ).toBe(false);
  });

  it("leaves other keys alone", () => {
    expect(
      isNewlineChord(new KeyboardEvent("keydown", { key: "a", shiftKey: true })),
    ).toBe(false);
  });

  /**
   * While an IME is composing, Enter confirms the candidate word. Turning that
   * into a line break would make the pane unusable in Japanese, Chinese and
   * Korean.
   */
  it("stays out of the way while an IME is composing", () => {
    const event = new KeyboardEvent("keydown", { key: "Enter", shiftKey: true });
    Object.defineProperty(event, "isComposing", { value: true });
    expect(isNewlineChord(event)).toBe(false);
  });
});

describe("writing a new line into a terminal pane", () => {
  it("sends ESC+CR instead of the carriage return that submits", () => {
    const term = fakeTerminal();
    installNewlineBridge(term);

    expect(term.press({ key: "Enter", shiftKey: true })).toBe(false);
    expect(term.sent).toEqual([NEWLINE_SEQUENCE]);
  });

  /**
   * Both bytes in ONE write: a parser tells "ESC as a modifier" from "Escape,
   * then Return" by whether they arrive together, and the split version cancels
   * the agent's prompt rather than extending it.
   */
  it("sends the escape and the return together", () => {
    const term = fakeTerminal();
    installNewlineBridge(term);

    term.press({ key: "Enter", altKey: true });

    expect(term.sent).toHaveLength(1);
    expect(term.sent[0]).toBe("\x1b\r");
  });

  it("leaves a plain Enter to xterm, so Enter still sends", () => {
    const term = fakeTerminal();
    installNewlineBridge(term);

    expect(term.press({ key: "Enter" })).toBe(true);
    expect(term.sent).toEqual([]);
  });

  /**
   * Without `preventDefault` the browser runs its default action, xterm's
   * separate keypress path sees a plain Enter, and the carriage return this
   * bridge exists to prevent goes out after all.
   */
  it("stops the browser from acting on the key as well", () => {
    let handler: (event: KeyboardEvent) => boolean = () => true;
    installNewlineBridge({
      attachCustomKeyEventHandler: (h) => {
        handler = h;
      },
      input: () => undefined,
    });

    const event = new KeyboardEvent("keydown", {
      key: "Enter",
      shiftKey: true,
      cancelable: true,
    });
    handler(event);

    expect(event.defaultPrevented).toBe(true);
  });

  it("writes one line per press, not one per keydown/keypress/keyup", () => {
    const term = fakeTerminal();
    installNewlineBridge(term);

    expect(term.press({ key: "Enter", shiftKey: true }, "keydown")).toBe(false);
    expect(term.press({ key: "Enter", shiftKey: true }, "keypress")).toBe(false);
    expect(term.press({ key: "Enter", shiftKey: true }, "keyup")).toBe(false);

    expect(term.sent).toEqual([NEWLINE_SEQUENCE]);
  });

  it("hands the chord back once the pane is gone", () => {
    const term = fakeTerminal();
    const dispose = installNewlineBridge(term);

    dispose();

    expect(term.press({ key: "Enter", shiftKey: true })).toBe(true);
    expect(term.sent).toEqual([]);
  });
});
