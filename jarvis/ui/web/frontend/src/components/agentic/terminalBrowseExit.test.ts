import { afterAll, afterEach, describe, expect, it, vi } from "vitest";
import { Terminal } from "@xterm/xterm";
import { installBrowseExit } from "./terminalBrowseExit";
import { installMouseSelection } from "./terminalMouseSelection";

const restoreCanvas = vi.hoisted(() => {
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = () => null;
  return () => { HTMLCanvasElement.prototype.getContext = original; };
});
afterAll(restoreCanvas);
const cleanups: (() => void)[] = [];
afterEach(() => { cleanups.splice(0).reverse().forEach((cleanup) => cleanup()); });

const FOOTER = "  Browsing · ↑↓/jk ←→/hl · ↵ rewind · esc";
const FRAME = ["Earlier output", "", "", "", "", "", "› Ask Codex to do anything",
  "", "  GPT-6-Astra xhigh", FOOTER].join("\r\n");

async function setup(output = FRAME, cols = 80) {
  // Real xterm parses the TUI screen. Only layout and transport are faked;
  // these tests do not launch a browser or send a turn to a provider.
  const terminal = new Terminal({ cols, rows: 10, allowProposedApi: true });
  const write = (text: string) => new Promise<void>((resolve) => terminal.write(text, resolve));
  await write(output);
  const container = document.createElement("div");
  const screen = document.createElement("div");
  screen.className = "xterm-screen";
  screen.getBoundingClientRect = () => ({
    x: 10, y: 20, left: 10, top: 20, right: 810, bottom: 220,
    width: 800, height: 200, toJSON: () => ({}),
  });
  container.appendChild(screen);
  document.body.appendChild(container);
  let onRender = () => {};
  const sent: string[] = [];
  let focused = 0;
  let cleared = 0;
  const disposeSelection = installMouseSelection(container, terminal, false);
  const dispose = installBrowseExit(container, {
    rows: terminal.rows, buffer: terminal.buffer,
    input: (data) => sent.push(data), focus: () => { focused++; },
    clearSelection: () => { cleared++; },
    onRender: (listener) => {
      onRender = () => listener({ start: 0, end: 9 });
      return { dispose: () => { onRender = () => {}; } };
    },
  });
  cleanups.push(() => { dispose(); disposeSelection(); terminal.dispose(); container.remove(); });
  const pointer = (type: string, row = 6, init: MouseEventInit = {}, target: EventTarget = screen) => {
    // jsdom has no PointerEvent constructor. Keep real DOM propagation and
    // mouse coordinates, adding the two pointer fields the bridge consumes.
    const event = new MouseEvent(type, {
      bubbles: true, clientX: 100, clientY: 20 + row * 20 + 10, button: 0, ...init,
    });
    Object.defineProperties(event, { pointerId: { value: 1 }, isPrimary: { value: true } });
    target.dispatchEvent(event);
  };
  const click = (row = 6, init: MouseEventInit = {}) => {
    pointer("pointerdown", row, init);
    screen.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0, ...init }));
    pointer("pointerup", row, init);
  };
  return { terminal, container, screen, write, pointer, click, sent, dispose,
    render: () => onRender(), focused: () => focused, cleared: () => cleared };
}

describe("leaving transcript browsing from the prompt", () => {
  it.each([51, 80])("restores input with one Escape in a %s-column pane", async (cols) => {
    const pane = await setup(FRAME, cols);
    pane.click();
    expect(pane.sent).toEqual(["\x1b"]);
    expect(pane.focused()).toBe(1);
    expect(pane.cleared()).toBe(1);
  });

  it("still works while the CLI tracks the mouse and xterm forces text selection", async () => {
    const pane = await setup();
    await pane.write("\x1b[?1003h");
    expect(pane.terminal.modes.mouseTrackingMode).toBe("any");
    pane.click();
    expect(pane.sent).toEqual(["\x1b"]);
  });

  it("does not send another Escape until browsing has visibly ended", async () => {
    const pane = await setup();
    pane.click(); pane.render(); pane.click();
    expect(pane.sent).toEqual(["\x1b"]);
    await pane.write("\x1b[10;1H\x1b[2K  ? for shortcuts");
    pane.render(); pane.click();
    expect(pane.sent).toEqual(["\x1b"]);
    await pane.write(`\x1b[10;1H\x1b[2K${FOOTER}`);
    pane.render(); pane.click();
    expect(pane.sent).toEqual(["\x1b", "\x1b"]);
  });

  it.each([0, 5, 7, 8, 9])("preserves output, padding and footer clicks on row %s", async (row) => {
    const pane = await setup(); pane.click(row);
    expect(pane.sent).toEqual([]);
  });

  it.each([{ shiftKey: true }, { altKey: true }, { ctrlKey: true }, { metaKey: true },
    { button: 1 }, { button: 2 }])("preserves modified and non-primary clicks: %j", async (init) => {
    const pane = await setup(); pane.click(6, init);
    expect(pane.sent).toEqual([]);
  });

  it("preserves a selection drag even when it ends where it started", async () => {
    const pane = await setup();
    pane.pointer("pointerdown");
    pane.pointer("pointermove", 6, { clientX: 160 });
    pane.pointer("pointerup");
    expect(pane.sent).toEqual([]);
    expect(pane.cleared()).toBe(0);
  });

  it("ignores pointer motion when no prompt click is pending", async () => {
    const pane = await setup();
    document.body.dispatchEvent(new Event("pointermove", { bubbles: true }));
    pane.pointer("pointermove", 0);
    expect(pane.sent).toEqual([]);
  });

  it.each(["pointercancel", "blur"])("cancels a pending click on %s", async (type) => {
    const pane = await setup(); pane.pointer("pointerdown");
    if (type === "blur") window.dispatchEvent(new Event(type));
    else pane.pointer(type);
    pane.pointer("pointerup");
    expect(pane.sent).toEqual([]);
  });

  it("does not act if the mode changes during the click", async () => {
    const pane = await setup(); pane.pointer("pointerdown");
    await pane.write("\x1b[10;1H\x1b[2K  Working · esc to interrupt");
    pane.pointer("pointerup");
    expect(pane.sent).toEqual([]);
  });

  it.each(["Working · esc to interrupt", "? for shortcuts", "Browsing the repository",
    "Browsing · ↑↓/jk ←→/hl · ↵ rewind", "Permission required · esc to cancel"])(
    "never interrupts another mode: %s", async (footer) => {
      const pane = await setup(FRAME.replace(FOOTER, footer)); pane.click();
      expect(pane.sent).toEqual([]);
    });

  it("does not treat a quoted footer in output as an active browse mode", async () => {
    const pane = await setup(FRAME.replace("Earlier output", FOOTER).replace(/Browsing[^\n]+esc$/, "? for shortcuts"));
    pane.click(); expect(pane.sent).toEqual([]);
  });

  it("does not act on history scrolled above the live bottom", async () => {
    const pane = await setup(`old\r\n`.repeat(20) + FRAME);
    // Without a rendered viewport, xterm's scrollLines does not move it.
    const buffer = pane.terminal.buffer.active;
    Object.defineProperty(buffer, "viewportY", { get: () => buffer.baseY - 5 });
    expect(pane.terminal.buffer.active.viewportY).toBeLessThan(buffer.baseY);
    pane.click(); expect(pane.sent).toEqual([]);
  });

  it("does not interpret a press outside the screen as a prompt click", async () => {
    const pane = await setup();
    pane.pointer("pointerdown", 6, {}, pane.container);
    pane.pointer("pointerup");
    pane.click(6, { clientX: 900 });
    expect(pane.sent).toEqual([]);
  });

  it("removes all listeners when the terminal is disposed", async () => {
    const pane = await setup(); pane.pointer("pointerdown"); pane.dispose();
    pane.pointer("pointerup"); pane.click(); pane.render();
    expect(pane.sent).toEqual([]);
  });
});
