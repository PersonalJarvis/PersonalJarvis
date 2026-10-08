import { act, cleanup, render } from "@testing-library/react";
import type { Terminal } from "@xterm/xterm";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const harness = vi.hoisted(() => ({
  terminal: null as Terminal | null,
  handlers: null as null | {
    onOutput(text: string): void;
    onReplay(text: string, awaitRepaint?: boolean): void;
    onGeometry(size: { cols: number; rows: number }): void;
  },
  size: { cols: 80, rows: 24 },
}));

// Exercise the installed xterm parser and buffer, without starting a browser or
// renderer. Only browser measurement and the PTY transport are stand-ins.
vi.mock("@xterm/xterm", async (importOriginal) => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  const original = await importOriginal<typeof import("@xterm/xterm")>();
  return {
    ...original,
    Terminal: class extends original.Terminal {
      constructor(options: ConstructorParameters<typeof original.Terminal>[0]) {
        super(options);
        harness.terminal = this;
      }
      open() {}
      loadAddon() {}
      focus() {}
      getSelection() { return ""; }
      onSelectionChange = () => ({ dispose() {} });
    },
  };
});
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    proposeDimensions() { return { ...harness.size }; }
    fit() { harness.terminal?.resize(harness.size.cols, harness.size.rows); }
  },
}));
vi.mock("./terminalRenderer", () => ({
  attachTerminalRenderer: () => ({ dispose() {} }),
  clearTerminalTextureAtlas() {},
}));
vi.mock("@/lib/terminalFont", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/terminalFont")>(),
  alignTerminalCells() {},
  terminalFontSettled: () => true,
  syncTerminalFont: () => () => undefined,
}));
vi.mock("./paneSocket", () => ({
  openPaneSocket: (options: { cols: number; rows: number }, handlers: typeof harness.handlers) => {
    // The real socket reads the handshake size into its URL on connect.
    void [options.cols, options.rows];
    harness.handlers = handlers;
    return { send: () => true, close() {} };
  },
}));
vi.mock("./paneFileDrag", () => ({
  usePaneFileDrag: () => ({ dragging: false, handlers: {} }),
}));
vi.mock("@/lib/editActions", () => ({ attachTerminalBridge() {} }));

import { AgenticTerminal } from "./AgenticTerminal";

function flush(term: Terminal, text = ""): Promise<void> {
  return new Promise((resolve) => term.write(text, resolve));
}

function screenRows(term: Terminal): string[] {
  const buffer = term.buffer.active;
  return Array.from({ length: term.rows }, (_, row) =>
    buffer.getLine(buffer.baseY + row)?.translateToString(true, 0, term.cols) ?? "",
  );
}

const oldFrame = "\x1b[?1049h\x1b[2J\x1b[H" +
  "A".repeat(80) + "\r\n" + "B".repeat(80) +
  "\r\nWorking\r\nTip: keep reading\x1b[23;1H> prompt\x1b[24;1Hfooter";
const newFrame = "\x1b[23;1H\x1b[J\x1b[47;1H> prompt\x1b[48;1Hfooter";

describe("pane geometry in the PTY output stream", () => {
  beforeEach(() => {
    harness.size = { cols: 80, rows: 24 };
    harness.handlers = null;
    harness.terminal = null;
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      disconnect() {}
    });
    vi.spyOn(document, "hasFocus").mockReturnValue(false);
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(800);
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(500);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const pane = (active = true, appearance: "light" | "dark" = "dark") => (
    <AgenticTerminal name="Geometry" displayName="Claude Code"
      appearance={appearance} fontSize={13} active={active} />
  );

  it.each(["light", "dark"] as const)(
    "parses earlier output at its original size in %s panes",
    async (appearance) => {
      const { Terminal: RealTerminal } = await vi.importActual<typeof import("@xterm/xterm")>("@xterm/xterm");
      const reference = new RealTerminal({ cols: 80, rows: 24, allowProposedApi: true });
      try {
        await flush(reference, oldFrame);
        reference.resize(40, 48);
        await flush(reference, newFrame);
        render(pane(true, appearance));
        const term = harness.terminal!;
        await act(async () => {
          // These messages arrive in order, before xterm's asynchronous parser
          // starts. Resizing synchronously parses old 80-column rows at 40.
          harness.handlers!.onOutput(oldFrame);
          harness.handlers!.onGeometry({ cols: 40, rows: 48 });
          harness.handlers!.onOutput(newFrame);
          await flush(term);
        });
        expect(screenRows(term)).toEqual(screenRows(reference));
        expect(term.cols).toBe(40);
        expect(term.rows).toBe(48);
      } finally {
        reference.dispose();
      }
    },
  );

  it("drains a hidden workspace's old output before accepting its new geometry", async () => {
    const view = render(pane(false));
    const term = harness.terminal!;
    await act(async () => {
      harness.handlers!.onOutput(oldFrame);
      harness.handlers!.onGeometry({ cols: 40, rows: 48 });
      await flush(term);
    });
    expect(screenRows(term)[2]).toBe("Working");
    expect(screenRows(term)[3]).toBe("Tip: keep reading");
    view.rerender(pane(true));
    await act(async () => { await flush(term); });
    expect(screenRows(term)[2]).toBe("Working");
  });

  /*
   * Reported 2026-10-06: the window reloaded while the IDE was on the thread
   * layout, so every pane of the grid behind it mounted in a display:none box
   * and kept xterm's constructed 80x24. The server replays the agent's screen
   * before it reports the size that screen was drawn at, and the replay was
   * parsed into those 24 rows: every row below them landed on the last one,
   * the status line over the prompt box. The size report then grew the grid to
   * the agent's 40 rows with blank rows underneath, and nothing asked the
   * agent to paint again, because the PTY already had that size.
   */
  const tallFrame = "\x1b[?1049h\x1b[2J\x1b[H" +
    Array.from({ length: 36 }, (_, i) => `\x1b[${i + 1};1Hline ${i + 1}`).join("") +
    "\x1b[38;1H" + "-".repeat(68) + "\x1b[39;1H> prompt\x1b[40;1H  footer";

  it.each([
    ["mounted off the stage", false],
    ["in a window too small to measure", true],
  ] as const)("draws a replay at the agent's size when the pane mounted unmeasured (%s)", async (_label, active) => {
    const { Terminal: RealTerminal } = await vi.importActual<typeof import("@xterm/xterm")>("@xterm/xterm");
    const reference = new RealTerminal({ cols: 68, rows: 40, allowProposedApi: true });
    const width = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(0);
    const height = vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(0);
    try {
      await flush(reference, tallFrame);
      const view = render(pane(active));
      const term = harness.terminal!;
      term.options.windowsPty = { backend: "conpty" };
      expect([term.cols, term.rows]).toEqual([80, 24]);
      await act(async () => {
        // The order the server sends them in: the screen, then its size.
        harness.handlers!.onReplay(tallFrame);
        harness.handlers!.onGeometry({ cols: 68, rows: 40 });
        await flush(term);
      });
      expect([term.cols, term.rows]).toEqual([68, 40]);
      expect(screenRows(term)).toEqual(screenRows(reference));

      // Taking the stage at the same size keeps the screen as it is.
      width.mockReturnValue(800);
      height.mockReturnValue(500);
      harness.size = { cols: 68, rows: 40 };
      view.rerender(pane(true));
      await act(async () => { await flush(term); });
      expect(screenRows(term)).toEqual(screenRows(reference));
    } finally {
      reference.dispose();
    }
  });

  it("keeps a measured hidden pane's old output at the size it already holds", async () => {
    // Only a grid that never had a real size waits for the agent's: a pane
    // that was measured drains what it holds first, then follows.
    const view = render(pane(false));
    const term = harness.terminal!;
    await act(async () => {
      harness.handlers!.onReplay(oldFrame);
      harness.handlers!.onGeometry({ cols: 40, rows: 48 });
      await flush(term);
    });
    expect([term.cols, term.rows]).toEqual([40, 48]);
    expect(screenRows(term)[2]).toBe("Working");
    view.rerender(pane(true));
    await act(async () => { await flush(term); });
    expect(screenRows(term)[2]).toBe("Working");
  });

  it.each([undefined, { backend: "conpty" as const }])(
    "does not introduce a large gap when an old narrow frame is followed by a wider pane (%j)",
    async (windowsPty) => {
      harness.size = { cols: 40, rows: 48 };
      render(pane());
      const term = harness.terminal!;
      if (windowsPty) term.options.windowsPty = windowsPty;
      const content = "X".repeat(40 * 24);
      await act(async () => {
        harness.handlers!.onOutput(
          "\x1b[?1049h\x1b[2J\x1b[H" + content + "\r\nWorking\r\nTip",
        );
        harness.handlers!.onGeometry({ cols: 80, rows: 48 });
        harness.handlers!.onOutput("\x1b[47;1H> prompt\x1b[48;1Hfooter");
        await flush(term);
      });
      // Premature resize puts Working on row 12: twelve content rows vanish
      // into an extra blank band above the bottom-anchored prompt.
      expect(screenRows(term).slice(0, 24)).toEqual(Array(24).fill("X".repeat(40)));
      expect(screenRows(term)[24]).toBe("Working");
      expect(screenRows(term)[25]).toBe("Tip");
      expect(screenRows(term)[46]).toBe("> prompt");
      expect(screenRows(term)[47]).toBe("footer");
    },
  );

  it("keeps a rapid resize and return ordered even when the last size matches the current grid", async () => {
    render(pane());
    const term = harness.terminal!;
    await act(async () => {
      harness.handlers!.onOutput(oldFrame);
      harness.handlers!.onGeometry({ cols: 40, rows: 48 });
      harness.handlers!.onOutput("\x1b[5;1H" + "C".repeat(80));
      harness.handlers!.onGeometry({ cols: 80, rows: 24 });
      harness.handlers!.onOutput("\x1b[8;1H" + "D".repeat(80));
      await flush(term);
    });
    expect([term.cols, term.rows]).toEqual([80, 24]);
    expect(screenRows(term).slice(4, 6)).toEqual(["C".repeat(40), "C".repeat(40)]);
    expect(screenRows(term)[7]).toBe("D".repeat(80));
    expect(screenRows(term)[8]).toBe("");
  });

  it("preserves intentional empty rows and a reader's normal-buffer scrollback position", async () => {
    render(pane());
    const term = harness.terminal!;
    await act(async () => {
      harness.handlers!.onOutput(Array.from({ length: 80 }, (_, i) => `history ${i}\r\n`).join(""));
      await flush(term);
    });
    term.scrollToLine(10);
    const before = term.buffer.active.viewportY;
    await act(async () => {
      harness.handlers!.onOutput("\x1b[Htitle\x1b[12;1Hbody\x1b[24;1Hfooter");
      harness.handlers!.onGeometry({ cols: 80, rows: 24 });
      await flush(term);
    });
    expect(term.buffer.active.viewportY).toBe(before);
    expect(term.buffer.active.getLine(0)?.translateToString(true)).toBe("history 0");

    await act(async () => {
      harness.handlers!.onOutput("\x1b[?1049h\x1b[2J\x1b[Htitle\x1b[24;1Hfooter");
      harness.handlers!.onGeometry({ cols: 80, rows: 24 });
      await flush(term);
    });
    expect(screenRows(term)).toEqual(["title", ...Array(22).fill(""), "footer"]);
  });

  it("ignores a queued geometry callback after unmount", async () => {
    const view = render(pane());
    const term = harness.terminal!;
    const resize = vi.spyOn(term, "resize");
    const callbacks: (() => void)[] = [];
    vi.spyOn(term, "write").mockImplementation((_text, done) => { if (done) callbacks.push(done); });
    act(() => {
      harness.handlers!.onOutput(oldFrame);
      harness.handlers!.onGeometry({ cols: 40, rows: 48 });
    });
    expect(resize).not.toHaveBeenCalled();
    view.unmount();
    for (const done of callbacks) done();
    expect(resize).not.toHaveBeenCalled();
  });
});
