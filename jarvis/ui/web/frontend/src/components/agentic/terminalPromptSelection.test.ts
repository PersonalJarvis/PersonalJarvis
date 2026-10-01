import { afterAll, afterEach, describe, expect, it, vi } from "vitest";
import { Terminal } from "@xterm/xterm";
import type { IBufferRange } from "@xterm/xterm";
import { installPromptSelectionBridge } from "./terminalPromptSelection";

// These tests parse cells without rendering. xterm's colour fallback supports
// a missing canvas context; jsdom otherwise logs its unimplemented method.
const restoreCanvas = vi.hoisted(() => {
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = () => null;
  return () => { HTMLCanvasElement.prototype.getContext = original; };
});
afterAll(restoreCanvas);

const terminals: Terminal[] = [];
afterEach(() => { terminals.splice(0).forEach((term) => term.dispose()); });

/** Parse real terminal cells, with a fake selection/input transport. No browser. */
async function setup(output = "› hello world", isMac = false, cols = 40) {
  const terminal = new Terminal({ cols, rows: 10, allowProposedApi: true });
  terminals.push(terminal);
  const write = (text: string) => new Promise<void>((resolve) => terminal.write(text, resolve));
  await write(output);
  let selected: IBufferRange | undefined;
  let onSelection = () => {};
  let keyHandler = (_event: KeyboardEvent) => true;
  const sent: string[] = [];
  let listenerDisposed = false;
  let handlerDisposed = false;
  const cleanup = installPromptSelectionBridge({
    cols,
    buffer: terminal.buffer,
    getSelectionPosition: () => selected,
    select: (x, y, length) => {
      const end = y * cols + x + length;
      selected = { start: { x, y }, end: { x: end % cols, y: Math.floor(end / cols) } };
      onSelection();
    },
    clearSelection: () => { selected = undefined; onSelection(); },
    input: (data) => sent.push(data),
    onSelectionChange: (listener) => {
      onSelection = listener;
      return { dispose: () => { listenerDisposed = true; onSelection = () => {}; } };
    },
  }, (handler) => {
    keyHandler = handler;
    return () => { handlerDisposed = true; keyHandler = () => true; };
  }, isMac);
  return {
    write, sent, cleanup,
    selection: () => selected,
    disposed: () => listenerDisposed && handlerDisposed,
    select: (start: number, end: number, row = 0, endRow = row) => {
      selected = { start: { x: start, y: row }, end: { x: end, y: endRow } };
      onSelection();
    },
    press: (key: string, mods: KeyboardEventInit = {}, type = "keydown") => {
      const event = new KeyboardEvent(type, { key, cancelable: true, ...mods });
      return { passthrough: keyHandler(event), prevented: event.defaultPrevented };
    },
  };
}

describe("terminal prompt selection", () => {
  it.each(["Backspace", "Delete"])("deletes the whole selected draft with one %s press", async (key) => {
    const pane = await setup();
    pane.select(2, 13);
    expect(pane.press(key)).toEqual({ passthrough: false, prevented: true });
    expect(pane.sent).toEqual(["\x7f".repeat(11)]);
    expect(pane.selection()).toBeUndefined();
    pane.press(key, {}, "keyup");
    expect(pane.sent).toHaveLength(1);
  });

  it("deletes a middle selection while leaving the suffix in place", async () => {
    const pane = await setup();
    pane.select(2, 7);
    pane.press("Delete");
    expect(pane.sent).toEqual(["\x1b[D".repeat(6) + "\x7f".repeat(5)]);
  });

  it("moves right when the caret precedes the selected text", async () => {
    const pane = await setup("❯ hello world\x1b[11D");
    pane.select(8, 13);
    pane.press("Backspace");
    expect(pane.sent).toEqual(["\x1b[C".repeat(11) + "\x7f".repeat(5)]);
  });

  it.each([false, true])("selects only input using the platform select-all chord (mac=%s)", async (isMac) => {
    const pane = await setup("old output\r\n› hello world", isMac);
    expect(pane.press("a", isMac ? { metaKey: true } : { ctrlKey: true }).passthrough).toBe(false);
    expect(pane.selection()).toEqual({ start: { x: 2, y: 1 }, end: { x: 13, y: 1 } });
    pane.press("Backspace");
    expect(pane.sent).toEqual(["\x7f".repeat(11)]);
  });

  it("excludes the prompt marker and terminal padding from a full-row drag", async () => {
    const pane = await setup("  > hello     \x1b[5D");
    pane.select(0, 39);
    pane.press("Delete");
    expect(pane.sent).toEqual(["\x7f".repeat(5)]);
  });

  it("handles an input line wrapped by the terminal", async () => {
    const pane = await setup("› hello world", false, 10);
    pane.select(2, 3, 0, 1);
    pane.press("Backspace");
    expect(pane.sent).toEqual(["\x7f".repeat(11)]);
  });

  it("handles select-all ending at the last terminal column", async () => {
    const pane = await setup("› abcdefgh", false, 10);
    pane.press("a", { ctrlKey: true });
    pane.press("Delete");
    expect(pane.sent).toEqual(["\x7f".repeat(8)]);
  });

  it("edits a prompt drawn in the alternate screen too", async () => {
    const pane = await setup("\x1b[?1049h› hello");
    pane.select(2, 7);
    pane.press("Backspace");
    expect(pane.sent).toEqual(["\x7f".repeat(5)]);
  });

  it("counts wide and combining characters as editing units, not cells or bytes", async () => {
    const pane = await setup("› 界e\u0301z");
    pane.select(2, 5);
    pane.press("Delete");
    expect(pane.sent).toEqual(["\x1b[D" + "\x7f\x7f"]);
  });

  it("does not split a wide character", async () => {
    const pane = await setup("› 界z");
    pane.select(3, 4);
    expect(pane.press("Delete").passthrough).toBe(false);
    expect(pane.sent).toEqual([]);
  });

  it("never edits the draft when output is selected", async () => {
    const pane = await setup("earlier output\r\n› hello");
    pane.select(0, 7);
    expect(pane.press("Backspace").passthrough).toBe(false);
    expect(pane.sent).toEqual([]);
  });

  it("rejects a selection made before a CLI redraw changed the draft", async () => {
    const pane = await setup();
    pane.select(2, 13);
    await pane.write("\r\x1b[2K› new draft");
    pane.press("Delete");
    expect(pane.sent).toEqual([]);
  });

  it.each(["shell output", "› [Pasted text 20 lines]", "› [Image #1]"])(
    "leaves an unsupported editor alone: %s", async (output) => {
      const pane = await setup(output);
      expect(pane.press("a", { ctrlKey: true }).passthrough).toBe(true);
      pane.select(2, 7);
      pane.press("Delete");
      expect(pane.sent).toEqual([]);
    },
  );

  it("leaves ordinary editing, modified keys and IME composition to the CLI", async () => {
    const pane = await setup();
    expect(pane.press("Backspace").passthrough).toBe(true);
    pane.select(2, 7);
    for (const mods of [{ ctrlKey: true }, { altKey: true }, { metaKey: true }, { isComposing: true }]) {
      expect(pane.press("Backspace", mods).passthrough).toBe(true);
    }
    expect(pane.press("Enter").passthrough).toBe(true);
    expect(pane.sent).toEqual([]);
    pane.cleanup();
    expect(pane.disposed()).toBe(true);
    expect(pane.press("Delete").passthrough).toBe(true);
  });
});
