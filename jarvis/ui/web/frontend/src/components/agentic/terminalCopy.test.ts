import { describe, expect, it, vi } from "vitest";

import { installCopyBridge, isCopyChord } from "./terminalCopy";

function fakeTerminal(selection = "") {
  let handler: (event: KeyboardEvent) => boolean = () => true;
  const dispose = vi.fn();
  return {
    term: {
      attachCustomKeyEventHandler: (next: (event: KeyboardEvent) => boolean) => {
        handler = next;
        return dispose;
      },
      getSelection: () => selection,
      focus: vi.fn(),
    },
    press: (init: KeyboardEventInit) =>
      handler(new KeyboardEvent("keydown", { cancelable: true, ...init })),
    dispose,
  };
}

describe("recognising the copy chord", () => {
  it("uses Ctrl+C on Windows and Linux", () => {
    expect(
      isCopyChord(
        new KeyboardEvent("keydown", { key: "c", ctrlKey: true }),
        false,
      ),
    ).toBe(true);
  });

  it("uses Cmd+C on macOS and leaves Ctrl+C there as terminal interrupt", () => {
    expect(
      isCopyChord(
        new KeyboardEvent("keydown", { key: "c", metaKey: true }),
        true,
      ),
    ).toBe(true);
    expect(
      isCopyChord(
        new KeyboardEvent("keydown", { key: "c", ctrlKey: true }),
        true,
      ),
    ).toBe(false);
  });

  it("accepts the traditional shifted copy chord and ignores AltGr", () => {
    expect(
      isCopyChord(
        new KeyboardEvent("keydown", {
          key: "C",
          ctrlKey: true,
          shiftKey: true,
        }),
        false,
      ),
    ).toBe(true);
    expect(
      isCopyChord(
        new KeyboardEvent("keydown", {
          key: "c",
          ctrlKey: true,
          altKey: true,
        }),
        false,
      ),
    ).toBe(false);
  });
});

describe("copying from a terminal pane", () => {
  it("copies xterm's canvas selection and keeps the pane focused", async () => {
    const copy = vi.fn(async () => true);
    const { term, press } = fakeTerminal("selected output");
    installCopyBridge(term, { copy, isMac: false });

    expect(press({ key: "c", ctrlKey: true })).toBe(false);
    await vi.waitFor(() => expect(copy).toHaveBeenCalledWith("selected output"));
    await vi.waitFor(() => expect(term.focus).toHaveBeenCalledOnce());
  });

  it("swallows Ctrl+C without a selection so it cannot cancel Codex", () => {
    const copy = vi.fn(async () => true);
    const { term, press } = fakeTerminal();
    installCopyBridge(term, { copy, isMac: false });

    expect(press({ key: "c", ctrlKey: true })).toBe(false);
    expect(copy).not.toHaveBeenCalled();
  });

  it("leaves unrelated terminal control chords alone", () => {
    const copy = vi.fn(async () => true);
    const { term, press } = fakeTerminal("selected output");
    installCopyBridge(term, { copy, isMac: false });

    expect(press({ key: "z", ctrlKey: true })).toBe(true);
    expect(copy).not.toHaveBeenCalled();
  });

  it("reports clipboard failure and unregisters cleanly", async () => {
    const onUnavailable = vi.fn();
    const { term, press, dispose } = fakeTerminal("selected output");
    const cleanup = installCopyBridge(term, {
      copy: async () => false,
      isMac: false,
      onUnavailable,
    });

    press({ key: "c", ctrlKey: true });
    await vi.waitFor(() => expect(onUnavailable).toHaveBeenCalledOnce());
    cleanup();
    expect(dispose).toHaveBeenCalledOnce();
  });

  it("does not focus or report after the pane was disposed", async () => {
    let finishCopy: ((copied: boolean) => void) | undefined;
    const copy = () =>
      new Promise<boolean>((resolve) => {
        finishCopy = resolve;
      });
    const onUnavailable = vi.fn();
    const { term, press } = fakeTerminal("selected output");
    const cleanup = installCopyBridge(term, {
      copy,
      isMac: false,
      onUnavailable,
    });

    press({ key: "c", ctrlKey: true });
    cleanup();
    finishCopy?.(false);
    await Promise.resolve();
    await Promise.resolve();

    expect(onUnavailable).not.toHaveBeenCalled();
    expect(term.focus).not.toHaveBeenCalled();
  });
});
