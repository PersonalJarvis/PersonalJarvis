import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const terminalHarness = vi.hoisted(() => ({
  open: vi.fn(),
  host: { current: null as HTMLElement | null },
  observe: vi.fn(),
  fit: vi.fn(),
  /** What the terminal reports after a fit — a test moves it to grow the pane. */
  size: { cols: 80, rows: 24 },
  /**
   * The grid a tile holds at a given font size; `null` answers with `size`.
   *
   * Staged by the auto-shrink tests: the pane probes several text sizes while
   * fitting its font to a narrow tile (see `fitFontToTile`), and a fixed
   * answer could never tell it that shrinking helped.
   */
  sizeForFont: null as
    | ((fontSize: number) => { cols: number; rows: number })
    | null,
  /** Every frame the pane hands its socket. Returns whether it went out. */
  send: vi.fn<(payload: unknown) => boolean>(() => true),
  /** Every explicit grid resize — how the pane pins itself below the floors. */
  resize: vi.fn<(cols: number, rows: number) => void>(),
  /** The live socket's handlers, so a test can play a reconnect. */
  handlers: { current: null as Record<string, (...args: never[]) => void> | null },
  /** Everything the pane types into the terminal on the user's behalf. */
  input: vi.fn<(data: string) => void>(),
  /** xterm's single custom key handler, so a test can press a key. */
  keys: { current: null as ((event: KeyboardEvent) => boolean) | null },
  /** xterm's wheel arbiter, which keeps the wheel on terminal history. */
  wheel: { current: null as ((event: WheelEvent) => boolean) | null },
  /** Parser state the wheel arbiter reads — a test flips these directly. */
  modes: { mouseTrackingMode: "none" as "none" | "any" },
  bufferType: "normal" as "normal" | "alternate",
  scrollLines: vi.fn<(amount: number) => void>(),
  /** Custom CSI observers installed on xterm's parser. */
  csiHandlers: [] as {
    id: { prefix?: string; final: string };
    callback: (params: (number | number[])[]) => boolean;
  }[],
  focus: vi.fn(),
  scrollToBottom: vi.fn(),
  scrollToLine: vi.fn<(line: number) => void>(),
  viewport: { baseY: 0, viewportY: 0 },
  write: vi.fn(),
  visibilityAtWrite: [] as string[],
  deferWrite: false,
  writeCallbacks: [] as (() => void)[],
  /**
   * Every terminal this pane has built, oldest first.
   *
   * A pane replaces its terminal without remounting — the grid re-measuring,
   * a restart, a rename — and what the REPLACEMENT is built with is exactly
   * where a pane lost the reader's text size. So the double keeps the options
   * it was constructed with rather than starting from an empty object, and the
   * list makes the newest instance reachable from a test.
   */
  instances: [] as { options: Record<string, unknown> }[],
}));

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    get cols() {
      return terminalHarness.size.cols;
    }
    get rows() {
      return terminalHarness.size.rows;
    }
    get modes() {
      return terminalHarness.modes;
    }
    get buffer() {
      return {
        active: { type: terminalHarness.bufferType, ...terminalHarness.viewport },
      };
    }
    scrollLines(amount: number) {
      terminalHarness.scrollLines(amount);
    }
    options: Record<string, unknown>;
    unicode = { activeVersion: "" };

    constructor(options: Record<string, unknown> = {}) {
      this.options = { ...options };
      terminalHarness.instances.push(this);
    }

    // The pane silences xterm's own answers to the agent's protocol queries
    // (see ./terminalQueries). The double only has to accept the handlers.
    parser = {
      registerOscHandler: () => ({ dispose() {} }),
      registerEscHandler: () => ({ dispose() {} }),
      registerCsiHandler: (
        id: { prefix?: string; final: string },
        callback: (params: (number | number[])[]) => boolean,
      ) => {
        const entry = { id, callback };
        terminalHarness.csiHandlers.push(entry);
        return {
          dispose() {
            const index = terminalHarness.csiHandlers.indexOf(entry);
            if (index >= 0) terminalHarness.csiHandlers.splice(index, 1);
          },
        };
      },
    };

    loadAddon() {}
    open(host: HTMLElement) {
      terminalHarness.open(host);
      terminalHarness.host.current = host;
    }
    focus() {
      terminalHarness.focus();
    }
    paste() {}
    attachCustomKeyEventHandler(handler: (event: KeyboardEvent) => boolean) {
      terminalHarness.keys.current = handler;
    }
    attachCustomWheelEventHandler(handler: (event: WheelEvent) => boolean) {
      terminalHarness.wheel.current = handler;
    }
    input(data: string) {
      terminalHarness.input(data);
    }
    getSelection() {
      return "";
    }
    onData() {
      return { dispose() {} };
    }
    write(text: string, callback?: () => void) {
      terminalHarness.write(text);
      terminalHarness.visibilityAtWrite.push(
        terminalHarness.host.current?.style.visibility ?? "",
      );
      if (!callback) return;
      if (terminalHarness.deferWrite) terminalHarness.writeCallbacks.push(callback);
      else callback();
    }
    scrollToBottom() {
      terminalHarness.scrollToBottom();
      terminalHarness.viewport.viewportY = terminalHarness.viewport.baseY;
    }
    scrollToLine(line: number) {
      terminalHarness.scrollToLine(line);
      terminalHarness.viewport.viewportY = line;
    }
    reset() {}
    resize(cols: number, rows: number) {
      terminalHarness.resize(cols, rows);
    }
    dispose() {}
    clearTextureAtlas() {}
  },
}));

vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit() {
      terminalHarness.fit();
    }
    // The pane measures before it applies (see `sendResize`), so the double
    // answers with whatever size the test has staged — the same value fit()
    // would land on. With `sizeForFont` staged the answer depends on the
    // newest terminal's CURRENT font size, which is what lets a test watch
    // the auto-shrink walk (see `fitFontToTile`).
    proposeDimensions() {
      const byFont = terminalHarness.sizeForFont;
      if (byFont) {
        const last =
          terminalHarness.instances[terminalHarness.instances.length - 1];
        return { ...byFont(Number(last?.options.fontSize ?? 0)) };
      }
      return { ...terminalHarness.size };
    }
  },
}));
vi.mock("@xterm/addon-web-links", () => ({ WebLinksAddon: class {} }));
vi.mock("@xterm/addon-canvas", () => ({ CanvasAddon: class {} }));
vi.mock("@xterm/addon-unicode11", () => ({ Unicode11Addon: class {} }));

vi.mock("./paneSocket", () => ({
  openPaneSocket: (
    _options: unknown,
    handlers: Record<string, (...args: never[]) => void>,
  ) => {
    terminalHarness.handlers.current = handlers;
    return {
      send: (payload: unknown) => terminalHarness.send(payload),
      close() {},
    };
  },
}));
vi.mock("./terminalPaste", () => ({
  installPasteBridge: () => () => undefined,
}));
vi.mock("./paneFileDrag", () => ({
  usePaneFileDrag: () => ({ dragging: false, handlers: {} }),
}));
vi.mock("@/lib/editActions", () => ({ attachTerminalBridge: () => undefined }));
vi.mock("@/lib/agenticIdeApi", () => ({ attachToTerminal: vi.fn() }));

import { AgenticTerminal, REBUILD_QUIET_MS } from "./AgenticTerminal";
import { PANE_CHROME } from "./terminalThemes";

/**
 * Past the quiet window a rebuilt pane waits out, plus the reveal frame behind
 * it. Bound to the real constant so tuning the window cannot silently turn
 * these assertions into "revealed eventually".
 */
const PAST_REBUILD = REBUILD_QUIET_MS + 40;

class ResizeObserverHarness implements ResizeObserver {
  constructor(_callback: ResizeObserverCallback) {}
  observe(target: Element) {
    terminalHarness.observe(target);
  }
  unobserve() {}
  disconnect() {}
}

describe("AgenticTerminal layout", () => {
  beforeEach(() => {
    terminalHarness.open.mockClear();
    terminalHarness.observe.mockClear();
    terminalHarness.fit.mockClear();
    terminalHarness.scrollToBottom.mockClear();
    terminalHarness.scrollToLine.mockClear();
    terminalHarness.viewport = { baseY: 0, viewportY: 0 };
    terminalHarness.write.mockClear();
    terminalHarness.host.current = null;
    terminalHarness.visibilityAtWrite = [];
    terminalHarness.deferWrite = false;
    terminalHarness.writeCallbacks = [];
    terminalHarness.wheel.current = null;
    terminalHarness.csiHandlers = [];
    terminalHarness.modes.mouseTrackingMode = "none";
    terminalHarness.bufferType = "normal";
    terminalHarness.scrollLines.mockClear();
    terminalHarness.sizeForFont = null;
    globalThis.ResizeObserver = ResizeObserverHarness;
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("measures an unpadded host inside the padded shrinking viewport", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
      />,
    );

    const host = screen.getByTestId("agentic-terminal-host-Dana");
    const viewport = host.parentElement;

    expect(viewport).not.toBeNull();
    expect(viewport?.className).toContain("min-h-0");
    // The inset is on the VIEWPORT, whatever it currently measures — the pane
    // frame has been tightened more than once and the exact values are a visual
    // decision. What must not change is WHICH element carries it: padding on
    // the host below would make FitAddon report a row the pane cannot show.
    expect(viewport?.className).toMatch(/(?:^|\s)px-[\d.]+/);
    expect(viewport?.className).toMatch(/(?:^|\s)pb-[\d.]+/);
    expect(viewport?.className).toMatch(/(?:^|\s)pt-[\d.]+/);
    expect(host.className).toContain("h-full");
    expect(host.className).toContain("min-h-0");
    expect(host.className).not.toMatch(/(?:^|\s)p[trblxy]?-/);
    expect(terminalHarness.open).toHaveBeenCalledWith(host);
    expect(terminalHarness.observe).toHaveBeenCalledWith(host);
    expect(terminalHarness.wheel.current).not.toBeNull();
  });

  it("keeps conversation history in the header without a scrollbar overlay", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
      />,
    );

    expect(screen.getByTestId("pane-conversation-Dana")).toBeTruthy();
    expect(screen.queryByRole("scrollbar")).toBeNull();
    expect(screen.queryByTestId("pane-scroll-history-Dana")).toBeNull();
  });

  it("keeps the wheel on terminal history even while the CLI tracks the mouse", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
      />,
    );

    // Plain pane: xterm's native wheel behaviour is already right.
    expect(
      terminalHarness.wheel.current?.(new WheelEvent("wheel", { deltaY: 120 })),
    ).toBe(true);
    expect(terminalHarness.scrollLines).not.toHaveBeenCalled();

    // A normal-buffer CLI that negotiates mouse tracking must NOT receive the
    // wheel as mouse reports — the wheel keeps scrolling xterm's history, so
    // scrolling behaves identically in every provider and every CLI mode.
    terminalHarness.modes.mouseTrackingMode = "any";
    expect(
      terminalHarness.wheel.current?.(new WheelEvent("wheel", { deltaY: 120 })),
    ).toBe(false);
    expect(terminalHarness.scrollLines).toHaveBeenCalledWith(3);
    expect(terminalHarness.input).not.toHaveBeenCalled();

    // A true alternate-screen app (vim, less) keeps its negotiated protocols.
    terminalHarness.bufferType = "alternate";
    terminalHarness.scrollLines.mockClear();
    expect(
      terminalHarness.wheel.current?.(new WheelEvent("wheel", { deltaY: 120 })),
    ).toBe(true);
    expect(terminalHarness.scrollLines).not.toHaveBeenCalled();
  });

  it("waits for the area-aware grid measurement before opening the PTY", () => {
    const view = render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        geometryReady={false}
      />,
    );

    expect(terminalHarness.open).not.toHaveBeenCalled();

    view.rerender(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        geometryReady
      />,
    );

    expect(terminalHarness.open).toHaveBeenCalled();
  });

  it("refits and follows the live tail before a hidden chat pane is shown", () => {
    const view = render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        active={false}
      />,
    );
    const host = screen.getByTestId("agentic-terminal-host-Dana");
    Object.defineProperty(host, "clientWidth", { configurable: true, value: 600 });
    Object.defineProperty(host, "clientHeight", { configurable: true, value: 400 });
    terminalHarness.fit.mockClear();
    terminalHarness.scrollToBottom.mockClear();

    view.rerender(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        active
      />,
    );

    expect(terminalHarness.fit).toHaveBeenCalled();
    expect(terminalHarness.scrollToBottom).toHaveBeenCalled();
  });

  it("restores a scrolled-back viewport when switching away and back", () => {
    const view = render(
      <AgenticTerminal
        name="Dana"
        displayName="Codex"
        appearance="dark"
        fontSize={13}
        active
      />,
    );
    terminalHarness.viewport = { baseY: 240, viewportY: 84 };
    terminalHarness.scrollToBottom.mockClear();
    terminalHarness.scrollToLine.mockClear();

    view.rerender(
      <AgenticTerminal
        name="Dana"
        displayName="Codex"
        appearance="dark"
        fontSize={13}
        active={false}
      />,
    );
    view.rerender(
      <AgenticTerminal
        name="Dana"
        displayName="Codex"
        appearance="dark"
        fontSize={13}
        active
      />,
    );

    expect(terminalHarness.scrollToLine).toHaveBeenCalledWith(84);
    expect(terminalHarness.scrollToBottom).not.toHaveBeenCalled();
  });

  it("keeps prompt output parked until an inactive chat pane is selected", () => {
    const view = render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        active={false}
      />,
    );
    terminalHarness.write.mockClear();

    act(() => {
      terminalHarness.handlers.current?.onPrompt?.(
        { text: "Run the tests", at: 1, chars: 13 } as never,
      );
      terminalHarness.handlers.current?.onOutput?.("working" as never);
    });
    expect(terminalHarness.write).not.toHaveBeenCalled();

    view.rerender(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        active
      />,
    );

    expect(terminalHarness.write).toHaveBeenCalledWith("working");
  });

  it("does not paint a reactivated chat pane before its live tail is ready", () => {
    vi.useFakeTimers();
    terminalHarness.deferWrite = true;
    const view = render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        active={false}
      />,
    );
    const region = screen.getByTestId("agentic-terminal-host-Dana").parentElement;

    act(() => {
      terminalHarness.handlers.current?.onOutput?.("new live output" as never);
    });
    view.rerender(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        active
      />,
    );

    expect(region?.className).toContain("invisible");
    expect(terminalHarness.writeCallbacks).toHaveLength(1);

    act(() => {
      // The held flush has parsed…
      terminalHarness.writeCallbacks.shift()?.();
    });
    // …and the pane placed a queue barrier behind it: an earlier write (a
    // replay flushed while hidden) may still be mid-parse, and the curtain
    // must not lift onto its tail printing.
    expect(terminalHarness.write).toHaveBeenLastCalledWith("");
    expect(terminalHarness.writeCallbacks).toHaveLength(1);

    act(() => {
      terminalHarness.writeCallbacks.shift()?.();
      vi.advanceTimersByTime(PAST_REBUILD);
    });

    expect(terminalHarness.scrollToBottom).toHaveBeenCalled();
    expect(region?.className).not.toContain("invisible");
  });

  it("hides an active pane while a replay rebuilds it, then reveals it at the tail", () => {
    vi.useFakeTimers();
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Codex"
        appearance="dark"
        fontSize={13}
        active
      />,
    );
    const region = screen.getByTestId("agentic-terminal-host-Dana").parentElement;
    act(() => {
      vi.advanceTimersByTime(20);
    });
    expect(region?.className).not.toContain("invisible");

    // A normal-buffer CLI's replay is its whole scrollback, parsed in slices:
    // painted onto a visible surface it prints top to bottom with the
    // viewport chasing it. The pane must hide until the tail scroll landed.
    terminalHarness.deferWrite = true;
    terminalHarness.scrollToBottom.mockClear();
    act(() => {
      terminalHarness.handlers.current?.onReplay?.(
        "the whole recorded session" as never,
      );
    });
    // Captured INSIDE `term.write`, before React gets another render. This is
    // the timing the real WebView exposed: a state-only curtain arrived after
    // xterm had already begun painting the replay.
    expect(terminalHarness.visibilityAtWrite.at(-1)).toBe("hidden");
    expect(screen.getByTestId("agentic-terminal-host-Dana").style.visibility).toBe(
      "hidden",
    );
    expect(region?.className).toContain("invisible");
    expect(terminalHarness.writeCallbacks).toHaveLength(1);

    act(() => {
      terminalHarness.writeCallbacks.shift()?.();
      vi.advanceTimersByTime(PAST_REBUILD);
    });

    expect(terminalHarness.scrollToBottom).toHaveBeenCalled();
    expect(screen.getByTestId("agentic-terminal-host-Dana").style.visibility).toBe("");
    expect(region?.className).not.toContain("invisible");
  });

  it("stays hidden while the post-replay repaint is still arriving", () => {
    // The replay is only half the rebuild: the server answers a truncated or
    // re-based one by nudging the agent into painting its whole screen again
    // (`SessionRegistry._nudge_repaint`), and that second screen lands AFTER
    // the replay parsed. Revealing in between is what still put the repaint in
    // front of the reader — a Codex pane opening on the top of its history and
    // racing down — even with the replay curtain working exactly as designed.
    vi.useFakeTimers();
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Codex"
        appearance="dark"
        fontSize={13}
        active
      />,
    );
    const region = screen.getByTestId("agentic-terminal-host-Dana").parentElement;
    act(() => vi.advanceTimersByTime(PAST_REBUILD));

    terminalHarness.deferWrite = true;
    act(() => {
      terminalHarness.handlers.current?.onReplay?.("recorded session" as never);
      terminalHarness.writeCallbacks.shift()?.();
    });
    expect(region?.className).toContain("invisible");

    // The repaint arrives mid-window and restarts it — the pane keeps waiting
    // rather than revealing on the schedule the replay alone would have set.
    act(() => {
      vi.advanceTimersByTime(REBUILD_QUIET_MS - 20);
      terminalHarness.handlers.current?.onOutput?.("the repainted screen" as never);
      vi.advanceTimersByTime(REBUILD_QUIET_MS - 20);
    });
    expect(region?.className).toContain("invisible");

    act(() => vi.advanceTimersByTime(PAST_REBUILD));
    expect(region?.className).not.toContain("invisible");
  });

  it("reveals a pane whose agent never stops talking", () => {
    // The quiet window assumes the redraw ends. An agent streaming an answer
    // never goes quiet, and waiting on it would trade a visible scroll for a
    // pane that simply does not come back.
    vi.useFakeTimers();
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Codex"
        appearance="dark"
        fontSize={13}
        active
      />,
    );
    const region = screen.getByTestId("agentic-terminal-host-Dana").parentElement;
    act(() => vi.advanceTimersByTime(PAST_REBUILD));

    terminalHarness.deferWrite = true;
    act(() => {
      terminalHarness.handlers.current?.onReplay?.("recorded session" as never);
      terminalHarness.writeCallbacks.shift()?.();
    });
    expect(region?.className).toContain("invisible");

    act(() => {
      // Chatty enough that the quiet window never once elapses.
      for (let tick = 0; tick < 12; tick += 1) {
        vi.advanceTimersByTime(REBUILD_QUIET_MS - 40);
        terminalHarness.handlers.current?.onOutput?.("still working…" as never);
      }
      vi.advanceTimersByTime(40);
    });

    expect(region?.className).not.toContain("invisible");
  });

  it("restores a scrolled-back viewport after replaying a Codex terminal", () => {
    vi.useFakeTimers();
    terminalHarness.deferWrite = true;
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Codex"
        appearance="dark"
        fontSize={13}
        active
      />,
    );
    act(() => {
      vi.advanceTimersByTime(20);
    });
    terminalHarness.viewport = { baseY: 240, viewportY: 84 };
    terminalHarness.scrollToBottom.mockClear();
    terminalHarness.scrollToLine.mockClear();

    act(() => {
      terminalHarness.handlers.current?.onReplay?.("recorded session" as never);
    });
    // The rebuilt buffer can be longer than the one the reader left.
    terminalHarness.viewport = { baseY: 260, viewportY: 260 };
    act(() => {
      terminalHarness.writeCallbacks.shift()?.();
      vi.advanceTimersByTime(20);
    });

    expect(terminalHarness.scrollToLine).toHaveBeenCalledWith(84);
    expect(terminalHarness.scrollToBottom).not.toHaveBeenCalled();
  });

  it("does not let an older replay reveal a newer replay", () => {
    vi.useFakeTimers();
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Codex"
        appearance="dark"
        fontSize={13}
        active
      />,
    );
    const region = screen.getByTestId("agentic-terminal-host-Dana").parentElement;
    act(() => vi.advanceTimersByTime(20));

    terminalHarness.deferWrite = true;
    act(() => {
      terminalHarness.handlers.current?.onReplay?.("older replay" as never);
      terminalHarness.handlers.current?.onReplay?.("newer replay" as never);
    });
    expect(region?.className).toContain("invisible");
    expect(terminalHarness.writeCallbacks).toHaveLength(2);

    act(() => {
      terminalHarness.writeCallbacks.shift()?.();
      vi.advanceTimersByTime(PAST_REBUILD);
    });
    expect(region?.className).toContain("invisible");

    act(() => {
      terminalHarness.writeCallbacks.shift()?.();
      vi.advanceTimersByTime(PAST_REBUILD);
    });
    expect(region?.className).not.toContain("invisible");
  });

  it("does not let a stale replay frame bypass a new stage barrier", () => {
    vi.useFakeTimers();
    const view = render(
      <AgenticTerminal
        name="Dana"
        displayName="Codex"
        appearance="dark"
        fontSize={13}
        active
      />,
    );
    const region = screen.getByTestId("agentic-terminal-host-Dana").parentElement;
    act(() => vi.advanceTimersByTime(20));

    terminalHarness.deferWrite = true;
    act(() => {
      terminalHarness.handlers.current?.onReplay?.("recorded session" as never);
      terminalHarness.writeCallbacks.shift()?.();
    });
    // The replay completion has scheduled its reveal frame, but a stage switch
    // supersedes it before that frame gets to paint.
    view.rerender(
      <AgenticTerminal
        name="Dana"
        displayName="Codex"
        appearance="dark"
        fontSize={13}
        active={false}
      />,
    );
    view.rerender(
      <AgenticTerminal
        name="Dana"
        displayName="Codex"
        appearance="dark"
        fontSize={13}
        active
      />,
    );
    expect(terminalHarness.writeCallbacks).toHaveLength(1);

    act(() => vi.advanceTimersByTime(PAST_REBUILD));
    expect(region?.className).toContain("invisible");

    act(() => {
      terminalHarness.writeCallbacks.shift()?.();
      vi.advanceTimersByTime(PAST_REBUILD);
    });
    expect(region?.className).not.toContain("invisible");
  });
});

describe("pane keyboard", () => {
  beforeEach(() => {
    terminalHarness.input.mockClear();
    terminalHarness.keys.current = null;
    globalThis.ResizeObserver = ResizeObserverHarness;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  /**
   * The reported bug: Shift+Enter sent the half-written instruction, because
   * every modifier combination of Enter reaches a terminal as the same
   * carriage return. See ./terminalNewline for the sequence.
   */
  it("breaks the line on Shift+Enter instead of sending the instruction", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
      />,
    );

    const press = terminalHarness.keys.current;
    expect(press).not.toBeNull();
    const claimed = press?.(
      new KeyboardEvent("keydown", { key: "Enter", shiftKey: true }),
    );

    expect(claimed).toBe(false);
    expect(terminalHarness.input).toHaveBeenCalledWith("\x1b\r");
  });

  it("leaves a plain Enter to xterm, so Enter still sends", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
      />,
    );

    const claimed = terminalHarness.keys.current?.(
      new KeyboardEvent("keydown", { key: "Enter" }),
    );

    expect(claimed).toBe(true);
    expect(terminalHarness.input).not.toHaveBeenCalled();
  });

  it("keeps Ctrl+C out of the PTY even when nothing is selected", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Codex"
        appearance="dark"
        fontSize={13}
      />,
    );

    const claimed = terminalHarness.keys.current?.(
      new KeyboardEvent("keydown", { key: "c", ctrlKey: true }),
    );

    expect(claimed).toBe(false);
  });
});

describe("pane header recap", () => {
  beforeEach(() => {
    globalThis.ResizeObserver = ResizeObserverHarness;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the session recap instead of the agent name", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        recap="Running pytest tests/unit/test_login.py"
        recapDetail='Last asked to: "Fix the failing login test". Working now, last output 4s ago: Running pytest tests/unit/test_login.py.'
        appearance="dark"
        fontSize={13}
      />,
    );

    expect(screen.getByTestId("pane-recap-Dana").textContent).toBe(
      "Running pytest tests/unit/test_login.py",
    );
    // The agent name is not gone, only moved out of the header line.
    expect(screen.queryByTestId("pane-agent-Dana")).toBeNull();
  });

  it("opens the longer recap in a card the header line controls", async () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        recap="Running pytest tests/unit/test_login.py"
        recapDetail='Last asked to: "Fix the failing login test". Working now: Running pytest tests/unit/test_login.py.'
        appearance="dark"
        fontSize={13}
      />,
    );

    const line = screen.getByTestId("pane-recap-Dana");
    expect(screen.queryByTestId("pane-recap-card-Dana")).toBeNull();

    fireEvent.click(line);
    const card = screen.getByTestId("pane-recap-card-Dana");

    expect(card.textContent).toContain("Fix the failing login test");
    // Which CLI runs here is named in the card rather than lost.
    expect(card.textContent).toContain("Claude Code");
    // A dialog, not a tooltip: it can be clicked into, its text selected, and
    // its buttons pressed — none of which the tooltip it replaces allowed.
    expect(card.getAttribute("role")).toBe("dialog");
    expect(line.getAttribute("aria-controls")).toBe(card.id);
    expect(line.getAttribute("aria-expanded")).toBe("true");
  });

  it("closes the card again on Escape", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        recap="Running pytest tests/unit/test_login.py"
        recapDetail="Where the work stands."
        appearance="dark"
        fontSize={13}
      />,
    );

    fireEvent.click(screen.getByTestId("pane-recap-Dana"));
    expect(screen.getByTestId("pane-recap-card-Dana")).toBeTruthy();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByTestId("pane-recap-card-Dana")).toBeNull();
  });

  it("falls back to the agent name while a pane has no recap yet", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
      />,
    );

    expect(screen.getByTestId("pane-agent-Dana").textContent).toBe(
      "Claude Code",
    );
    expect(screen.queryByTestId("pane-recap-Dana")).toBeNull();
  });

  it("repeats nothing when the long form says what the header line says", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        recap="Not started yet."
        recapDetail="Not started yet."
        appearance="dark"
        fontSize={13}
      />,
    );

    fireEvent.click(screen.getByTestId("pane-recap-Dana"));

    expect(screen.getByTestId("pane-recap-headline-Dana").textContent).toBe(
      "Not started yet.",
    );
    expect(screen.queryByTestId("pane-recap-detail-Dana")).toBeNull();
  });
});

describe("pane header actions", () => {
  beforeEach(() => {
    globalThis.ResizeObserver = ResizeObserverHarness;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("recedes on an unfocused pane but stays reachable by hover and keyboard", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        focused={false}
        onToggleMaximize={() => undefined}
        onSplit={() => undefined}
        onClose={() => undefined}
      />,
    );

    const actions = screen.getByTestId("pane-maximize-Dana").parentElement;

    expect(actions).not.toBeNull();
    // Hidden by opacity only — the buttons stay in the DOM, so a header hover
    // or tabbing into the cluster reveals the same elements this test finds.
    expect(actions?.className).toContain("opacity-0");
    expect(actions?.className).toContain("group-hover/header:opacity-100");
    expect(actions?.className).toContain("focus-within:opacity-100");
    expect(screen.getByTestId("pane-split-right-Dana")).toBeTruthy();
    expect(screen.getByTestId("pane-split-down-Dana")).toBeTruthy();
    expect(screen.getByTestId("pane-close-Dana")).toBeTruthy();
  });

  it("fills the workspace on a double-click of the title bar", () => {
    const onToggleMaximize = vi.fn();
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        onToggleMaximize={onToggleMaximize}
      />,
    );

    fireEvent.doubleClick(screen.getByTestId("pane-header-Dana"));

    expect(onToggleMaximize).toHaveBeenCalledTimes(1);
  });

  it("renames instead of maximizing when the call-sign is the target", () => {
    const onToggleMaximize = vi.fn();
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        onToggleMaximize={onToggleMaximize}
        onRename={async () => true}
      />,
    );

    // The call-sign is the more specific target and stops the event, so the
    // bar underneath never sees it — one gesture, one meaning.
    fireEvent.doubleClick(screen.getByText("Dana"));

    expect(screen.getByTestId("pane-rename-input-Dana")).toBeTruthy();
    expect(onToggleMaximize).not.toHaveBeenCalled();
  });

  it("leaves a double-click on one of its own buttons to that button", () => {
    const onToggleMaximize = vi.fn();
    const onClose = vi.fn();
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        onToggleMaximize={onToggleMaximize}
        onClose={onClose}
      />,
    );

    // Two clicks on Close are two closes, never a maximize — the same guard
    // the drag grip uses, for the same reason.
    fireEvent.doubleClick(screen.getByTestId("pane-close-Dana"));

    expect(onToggleMaximize).not.toHaveBeenCalled();
  });

  it("keeps every action visible on the focused pane", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        focused
        onToggleMaximize={() => undefined}
        onSplit={() => undefined}
        onClose={() => undefined}
      />,
    );

    const actions = screen.getByTestId("pane-maximize-Dana").parentElement;

    expect(actions).not.toBeNull();
    expect(actions?.className).toContain("opacity-100");
    expect(actions?.className).not.toContain("opacity-0 ");
  });
});

describe("pane split menu", () => {
  const CHOICES = [
    { name: "claude", displayName: "Claude Code", installed: true, kind: "cli" },
    { name: "codex", displayName: "Codex", installed: true, kind: "cli" },
    {
      name: "shell",
      displayName: "Plain Terminal",
      installed: true,
      kind: "shell",
      description: "PowerShell 7 — no agent, just a prompt",
    },
  ];

  beforeEach(() => {
    globalThis.ResizeObserver = ResizeObserverHarness;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("offers a plain terminal beside the coding agents", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        agents={CHOICES}
        onSplit={() => undefined}
      />,
    );

    fireEvent.click(screen.getByTestId("pane-split-right-Dana"));

    expect(screen.getByTestId("pane-split-right-Dana-claude").textContent).toContain(
      "Claude Code",
    );
    expect(screen.getByTestId("pane-split-right-Dana-codex").textContent).toContain(
      "Codex",
    );
    const plain = screen.getByTestId("pane-split-right-Dana-shell");
    expect(plain.textContent).toContain("Plain Terminal");
    // ...and it says what that actually opens, which is the whole difference.
    expect(plain.textContent).toContain("no agent");
  });

  it("splits with the plain-terminal entry the user picked", () => {
    const onSplit = vi.fn();
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        agents={CHOICES}
        onSplit={onSplit}
      />,
    );

    fireEvent.click(screen.getByTestId("pane-split-down-Dana"));
    fireEvent.click(screen.getByTestId("pane-split-down-Dana-shell"));

    expect(onSplit).toHaveBeenCalledWith("down", "shell");
  });

  it("disables the plain terminal on a host with no shell, and says why", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        agents={[
          CHOICES[0],
          CHOICES[1],
          { name: "shell", displayName: "Plain Terminal", installed: false, kind: "shell" },
        ]}
        onSplit={() => undefined}
      />,
    );

    fireEvent.click(screen.getByTestId("pane-split-right-Dana"));
    const plain = screen.getByTestId("pane-split-right-Dana-shell") as HTMLButtonElement;

    // Listed but unusable, so the absence explains itself instead of the entry
    // simply not being there — and in the terms of what is missing.
    expect(plain.disabled).toBe(true);
    expect(plain.textContent).toContain("no shell here");
  });
});

describe("pane refit", () => {
  /** jsdom measures nothing; the pane refuses to fit a host it reads as 0x0. */
  const giveTheHostASize = () => {
    for (const [property, value] of [
      ["clientWidth", 600],
      ["clientHeight", 400],
    ] as const) {
      Object.defineProperty(HTMLElement.prototype, property, {
        configurable: true,
        value,
      });
    }
  };

  const settle = () => {
    act(() => {
      vi.advanceTimersByTime(600);
    });
  };

  const pane = (maximized: boolean, active = true) => (
    <AgenticTerminal
      name="Dana"
      displayName="Claude Code"
      appearance="dark"
      fontSize={13}
      maximized={maximized}
      active={active}
    />
  );

  beforeEach(() => {
    vi.useFakeTimers();
    globalThis.ResizeObserver = ResizeObserverHarness;
    giveTheHostASize();
    terminalHarness.fit.mockClear();
    terminalHarness.send.mockClear();
    terminalHarness.send.mockImplementation(() => true);
    terminalHarness.resize.mockClear();
    terminalHarness.handlers.current = null;
    terminalHarness.size = { cols: 80, rows: 24 };
    terminalHarness.sizeForFont = null;
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
    Reflect.deleteProperty(HTMLElement.prototype, "clientHeight");
  });

  it("re-measures itself when the pane is maximized", () => {
    // The ResizeObserver harness never calls anyone back, which is the point:
    // this proves the pane no longer DEPENDS on that notification arriving.
    // When it went missing, the pane was maximized while the agent inside it
    // kept drawing at its old cell's width.
    const view = render(pane(false));
    settle();
    terminalHarness.fit.mockClear();

    view.rerender(pane(true));
    settle();

    expect(terminalHarness.fit).toHaveBeenCalled();
  });

  it("re-measures again when the pane is restored to its cell", () => {
    const view = render(pane(true));
    settle();
    terminalHarness.fit.mockClear();

    view.rerender(pane(false));
    settle();

    expect(terminalHarness.fit).toHaveBeenCalled();
  });

  it("does not re-announce a size the terminal process already has", () => {
    // Refitting is nearly free; telling the agent makes it redraw its whole
    // screen. A pane settles over several passes, so only changes go out.
    const view = render(pane(false));
    settle();
    terminalHarness.send.mockClear();

    view.rerender(pane(true));
    settle();

    expect(terminalHarness.send).not.toHaveBeenCalled();
  });

  it("reclaims the shared PTY geometry when the pane becomes active", () => {
    const view = render(pane(false, false));
    settle();
    terminalHarness.send.mockClear();

    view.rerender(pane(false, true));
    settle();

    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "claim",
      cols: 80,
      rows: 24,
    });
  });

  it("keeps offering a size the socket could not carry", () => {
    // A pane measured while its backend was restarting must not treat the
    // frame as delivered. Nothing measures a pane again on its own, so a size
    // counted as sent when it never left is lost for good — and the agent goes
    // on formatting for a size the pane no longer has.
    const view = render(pane(false));
    settle();

    terminalHarness.send.mockImplementation(() => false);
    terminalHarness.size.cols = 200;
    view.rerender(pane(true));
    settle();
    terminalHarness.send.mockClear();

    // Same size, still undelivered — so it goes out again rather than being
    // deduplicated away.
    terminalHarness.send.mockImplementation(() => true);
    view.rerender(pane(false));
    settle();

    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "r",
      cols: 200,
      rows: 24,
    });
  });

  it("never hands the agent a tile too narrow to draw in", () => {
    // The failure behind "working panes are shown as done" (maintainer,
    // 2026-08-09, thirteen panes open). A crowded grid measures ~17 columns
    // per cell — a CORRECT measurement, which is why the old 8x2 floor waved
    // it through. The agent, squeezed into a strip it cannot lay its
    // interface out in, stops drawing altogether; the status badge reads that
    // silence as a finished job, because movement is all it can read.
    //
    // So the measurement is CLAMPED to the floors, per dimension, and the
    // clamped size is what the agent hears: never the raw 17 columns, always
    // a frame it can still draw. The tile clips what it cannot show.
    const view = render(pane(false));
    settle();
    terminalHarness.send.mockClear();

    terminalHarness.size = { cols: 17, rows: 6 };
    view.rerender(pane(true));
    settle();

    expect(terminalHarness.send).not.toHaveBeenCalledWith(
      expect.objectContaining({ cols: 17 }),
    );
    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "r",
      cols: 60,
      rows: 15,
    });

    // A tile the agent can work in is still announced honestly.
    terminalHarness.size = { cols: 90, rows: 30 };
    view.rerender(pane(false));
    settle();

    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "r",
      cols: 90,
      rows: 30,
    });
  });

  it("keeps the local grid on the clamped size rather than the raw fit", () => {
    // Clamping what the agent HEARS is only half of the contract (test
    // above). The other half is the LOCAL grid: fit()ing xterm to the
    // 17-column tile while the agent lays its lines out for 60 re-wraps
    // every one of them at the narrower measure, and the TUI's cursor moves
    // then land on rows that no longer hold what they held when it drew them
    // — a five-pane grid came back as panes full of shredded one-word
    // fragments (reported 2026-08-10). So below a floor nothing is fit at
    // all: the grid takes the same clamped size the agent was told, and the
    // tile's overflow-hidden container clips the rest.
    const view = render(pane(false));
    settle();
    terminalHarness.fit.mockClear();
    terminalHarness.resize.mockClear();

    terminalHarness.size = { cols: 17, rows: 6 };
    view.rerender(pane(true));
    settle();

    expect(terminalHarness.fit).not.toHaveBeenCalled();
    expect(terminalHarness.resize).toHaveBeenCalledWith(60, 15);
  });

  it("keeps a narrow tile's honest height so the prompt stays visible", () => {
    // The floors fail one dimension at a time. A tile in a five-pane row
    // measures 33 columns and a perfectly honest 40 rows — and refusing the
    // WHOLE size left the PTY at a stale 64-row geometry, with the coding
    // CLI anchoring its input box to row 64 of a pane showing 37 (measured
    // live 2026-08-10: every pane in the workspace came up with no visible
    // prompt). Clamped per dimension, only the width is lifted to the floor;
    // the height the tile really has is what the agent hears, and its
    // bottom-anchored prompt box lands in the bottom visible row.
    const view = render(pane(false));
    settle();
    terminalHarness.send.mockClear();
    terminalHarness.resize.mockClear();

    terminalHarness.size = { cols: 33, rows: 40 };
    view.rerender(pane(true));
    settle();

    expect(terminalHarness.resize).toHaveBeenCalledWith(60, 40);
    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "r",
      cols: 60,
      rows: 40,
    });
  });

  it("shrinks its text until the floor grid fits a narrow tile", () => {
    // The reported 2026-08-11 pane: a tile too narrow for 60 columns at the
    // reader's size used to clip the right half of every line behind its edge
    // — the left ~25 characters of each 60-column line, reading as shredded
    // word-salad. The pane now walks its own text size down until the floor
    // grid fits the tile, so the same 60 columns read small instead of cut.
    const view = render(pane(false));
    settle();
    terminalHarness.send.mockClear();
    terminalHarness.fit.mockClear();

    // This tile holds 40 columns at the reader's 13px and the floor's 60 from
    // 10px down. The walk must land on 10 — the LARGEST size that fits, not
    // the smallest it may use.
    terminalHarness.sizeForFont = (fontSize) =>
      fontSize <= 10 ? { cols: 62, rows: 20 } : { cols: 40, rows: 20 };
    view.rerender(pane(true));
    settle();

    const term =
      terminalHarness.instances[terminalHarness.instances.length - 1];
    expect(term?.options.fontSize).toBe(10);
    // At the shrunken size the grid fits honestly — fit(), not a clamp.
    expect(terminalHarness.fit).toHaveBeenCalled();
    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "r",
      cols: 62,
      rows: 20,
    });
  });

  it("returns to the reader's text size when the tile widens again", () => {
    const view = render(pane(false));
    settle();
    terminalHarness.sizeForFont = (fontSize) =>
      fontSize <= 10 ? { cols: 62, rows: 20 } : { cols: 40, rows: 20 };
    view.rerender(pane(true));
    settle();
    const term =
      terminalHarness.instances[terminalHarness.instances.length - 1];
    expect(term?.options.fontSize).toBe(10);
    terminalHarness.send.mockClear();

    // The tile widens (a sibling closed, the pane was maximized) — the shrink
    // must not outlive its reason, or one crowded afternoon leaves the pane
    // small forever.
    terminalHarness.sizeForFont = () => ({ cols: 90, rows: 30 });
    view.rerender(pane(false));
    settle();

    expect(term?.options.fontSize).toBe(13);
    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "r",
      cols: 90,
      rows: 30,
    });
  });

  it("tells a fresh socket the pane's size whatever the last one heard", () => {
    render(pane(false));
    settle();
    terminalHarness.send.mockClear();

    // A reconnect gets a process that knows nothing about this pane, so the
    // size is announced again even though it has not changed.
    act(() => {
      terminalHarness.handlers.current?.onOpen?.();
      vi.advanceTimersByTime(600);
    });

    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "r",
      cols: 80,
      rows: 24,
    });
  });
});

/**
 * A pane opened by voice used to be an empty black rectangle for seconds on
 * end, and "open two more terminals" therefore looked like it had silently
 * failed (maintainer report 2026-07-28). The pane now says it is starting until
 * its agent draws something.
 */
describe("pane start-up feedback", () => {
  beforeEach(() => {
    globalThis.ResizeObserver = ResizeObserverHarness;
    terminalHarness.handlers.current = null;
    terminalHarness.focus.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("says which CLI it is starting while the pane is still blank", () => {
    render(
      <AgenticTerminal
        name="T5"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
      />,
    );

    expect(screen.getByTestId("agentic-pane-starting-T5").textContent).toContain(
      "Starting Claude Code",
    );
  });

  it("gets out of the way as soon as the agent draws its first byte", () => {
    render(
      <AgenticTerminal
        name="T5"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
      />,
    );

    act(() => {
      terminalHarness.handlers.current?.onOutput?.(
        "Claude Code v2.1.220" as never,
      );
    });

    expect(screen.queryByTestId("agentic-pane-starting-T5")).toBeNull();
  });

  it("does not let a hidden pane steal focus when it finishes connecting", () => {
    render(
      <AgenticTerminal
        name="T5"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        focused
        active={false}
      />,
    );

    act(() => {
      terminalHarness.handlers.current?.onReady?.(
        { resumed: false, reattached: false, lastPrompt: null } as never,
      );
    });

    expect(terminalHarness.focus).not.toHaveBeenCalled();
  });

  /**
   * The overlay must never cover a pane the user could otherwise act on: an
   * exited or unreachable pane has a restart button and a reason of its own,
   * and a hopeful spinner over either would be a lie.
   */
  it("stands down when the pane reports trouble rather than progress", () => {
    render(
      <AgenticTerminal
        name="T5"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
      />,
    );

    act(() => {
      terminalHarness.handlers.current?.onTrouble?.(
        "This terminal is no longer part of the open workspace." as never,
        false as never,
      );
    });

    expect(screen.queryByTestId("agentic-pane-starting-T5")).toBeNull();
  });
});

describe("renaming a pane", () => {
  beforeEach(() => {
    globalThis.ResizeObserver = ResizeObserverHarness;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("offers no rename control when the owner cannot save one", () => {
    render(
      <AgenticTerminal
        name="T1"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
      />,
    );

    // A pencil that opens an editor nothing can save would be worse than the
    // plain badge it replaces.
    expect(screen.queryByTestId("pane-rename-T1")).toBeNull();
  });

  it("saves the typed call-sign", async () => {
    const onRename = vi.fn(async () => true);
    render(
      <AgenticTerminal
        name="T1"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        onRename={onRename}
      />,
    );

    fireEvent.click(screen.getByTestId("pane-rename-T1"));
    const input = screen.getByTestId("pane-rename-input-T1") as HTMLInputElement;
    // It opens on the current name, so a small correction is not a retype.
    expect(input.value).toBe("T1");
    fireEvent.change(input, { target: { value: "Frontend" } });
    fireEvent.click(screen.getByTestId("pane-rename-save-T1"));

    await act(async () => undefined);
    expect(onRename).toHaveBeenCalledWith("Frontend");
    expect(screen.queryByTestId("pane-rename-input-T1")).toBeNull();
  });

  it("keeps the typing when the name was refused", async () => {
    const onRename = vi.fn(async () => false);
    render(
      <AgenticTerminal
        name="T1"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        onRename={onRename}
      />,
    );

    fireEvent.click(screen.getByTestId("pane-rename-T1"));
    fireEvent.change(screen.getByTestId("pane-rename-input-T1"), {
      target: { value: "Api" },
    });
    fireEvent.click(screen.getByTestId("pane-rename-save-T1"));

    await act(async () => undefined);
    // A duplicate call-sign is a name to CHANGE — throwing the typing away
    // would make the user retype the part that was fine.
    const input = screen.getByTestId("pane-rename-input-T1") as HTMLInputElement;
    expect(input.value).toBe("Api");
  });

  it("closes on Escape without saving", async () => {
    const onRename = vi.fn(async () => true);
    render(
      <AgenticTerminal
        name="T1"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        onRename={onRename}
      />,
    );

    fireEvent.click(screen.getByTestId("pane-rename-T1"));
    fireEvent.keyDown(screen.getByTestId("pane-rename-input-T1"), {
      key: "Escape",
    });

    await act(async () => undefined);
    expect(onRename).not.toHaveBeenCalled();
    expect(screen.queryByTestId("pane-rename-input-T1")).toBeNull();
  });

  it("does not call the backend when the name was not changed", async () => {
    const onRename = vi.fn(async () => true);
    render(
      <AgenticTerminal
        name="T1"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        onRename={onRename}
      />,
    );

    fireEvent.click(screen.getByTestId("pane-rename-T1"));
    fireEvent.click(screen.getByTestId("pane-rename-save-T1"));

    await act(async () => undefined);
    expect(onRename).not.toHaveBeenCalled();
    expect(screen.queryByTestId("pane-rename-input-T1")).toBeNull();
  });
});

/*
 * The toolbar's text size is an ACCESSIBILITY control: somebody who cannot
 * comfortably read 13px sets 20 once and expects every pane to be readable,
 * not the one they happen to be typing in.
 *
 * What broke that was invisible in every earlier test, because the terminal
 * double ignored the options it was constructed with: the pane froze the size
 * and theme it first rendered with and handed them to every terminal it built
 * afterwards. A pane replaces its terminal without remounting — the grid
 * re-measures and `geometryReady` flips, a pane is restarted or renamed — and
 * since the size effect fires on CHANGES, nothing came along afterwards to
 * correct the resurrected value. The panes rebuilt since the last change sat
 * at the startup size for good, next to the ones that were not.
 */
describe("terminal text size across a rebuild", () => {
  beforeEach(() => {
    terminalHarness.instances.length = 0;
    globalThis.ResizeObserver = ResizeObserverHarness;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const newest = () => terminalHarness.instances[terminalHarness.instances.length - 1];

  it("keeps the xterm canvas clear over the shared translucent pane shell", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
      />,
    );

    expect(newest().options.allowTransparency).toBe(true);
    // Alpha 0 keeps the canvas clear; the RGB is the ground the
    // minimum-contrast floor measures truecolor foregrounds against.
    expect(
      (newest().options.theme as Record<string, unknown>).background,
    ).toBe("rgba(18, 20, 26, 0)");
    expect(newest().options.minimumContrastRatio).toBe(4.5);
    expect(screen.getByTestId("agentic-pane-Dana").style.background).toBe(
      "rgba(10, 10, 10, 0.58)",
    );
  });

  it("builds a replacement terminal at the size the user is looking at", () => {
    const view = render(
      <AgenticTerminal name="Dana" displayName="Claude Code" appearance="dark" fontSize={13} />,
    );
    expect(newest().options.fontSize).toBe(13);

    // The reader turns the text up while the pane is live.
    view.rerender(
      <AgenticTerminal name="Dana" displayName="Claude Code" appearance="dark" fontSize={20} />,
    );
    expect(newest().options.fontSize).toBe(20);

    // ...and the grid is re-measured, which rebuilds the terminal underneath a
    // pane that never unmounted. This is the pane the user was NOT typing in.
    view.rerender(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={20}
        geometryReady={false}
      />,
    );
    view.rerender(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={20}
        geometryReady
      />,
    );

    expect(terminalHarness.instances.length).toBeGreaterThan(1);
    expect(newest().options.fontSize).toBe(20);
  });

  it("restates the size to a terminal restarted after the change", () => {
    const view = render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        restartToken={0}
      />,
    );
    // Copied now: the live restyle below writes the new theme onto THIS
    // instance too, so reading it afterwards would compare light against light.
    const openedWith = { ...(newest().options.theme as Record<string, unknown>) };
    view.rerender(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="light"
        fontSize={18}
        restartToken={0}
      />,
    );
    view.rerender(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="light"
        fontSize={18}
        restartToken={1}
      />,
    );

    // The theme travels with the size: both were frozen by the same ref, so a
    // restarted pane came back in the palette it opened with.
    expect(newest().options.fontSize).toBe(18);
    expect(newest().options.theme).not.toEqual(openedWith);
  });
});

/**
 * What a pane says about its own state, and where it says it.
 *
 * Both halves used to live somewhere that lost them. The reason a pane died was
 * written INTO the terminal, where the next thing drawn scrolls it away and the
 * one-line-per-kind-of-trouble guard means it is never written again; the way
 * out was a button in the hover-only action cluster. The badge and the notice
 * below are the durable versions of each.
 */
describe("pane status", () => {
  beforeEach(() => {
    globalThis.ResizeObserver = ResizeObserverHarness;
    terminalHarness.handlers.current = null;
    terminalHarness.write.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const pane = (props: Record<string, unknown> = {}) => (
    <AgenticTerminal
      name="Dana"
      displayName="Claude Code"
      appearance="dark"
      fontSize={13}
      {...props}
    />
  );

  it("opens carrying its connecting state, with no notice to answer yet", () => {
    render(pane());

    expect(screen.getByTestId("pane-status-Dana").dataset.status).toBe(
      "connecting",
    );
    expect(screen.getByTestId("pane-activity").dataset.icon).toBe("spinner");
    expect(screen.queryByTestId("pane-notice-Dana")).toBeNull();
  });

  /**
   * `live` is a property of the PIPE and true for nearly every pane nearly all
   * the time, so a standing dot on twelve headers marks nothing. It stays in
   * the DOM and fades in with the header's other controls.
   */
  it("keeps a healthy pane's badge quiet until the header is hovered", () => {
    render(pane());

    act(() => {
      terminalHarness.handlers.current?.onReady?.(
        { resumed: false, reattached: false, lastPrompt: null } as never,
      );
    });

    const badge = screen.getByTestId("pane-status-Dana");
    expect(badge.dataset.status).toBe("live");
    expect(badge.className).toContain("opacity-0");
    expect(badge.className).toContain("group-hover/header:opacity-60");
    expect(screen.queryByTestId("pane-notice-Dana")).toBeNull();
  });

  it("says what happened when the agent exits, and offers the way back", () => {
    const onRestart = vi.fn();
    render(pane({ onRestart }));

    act(() => {
      terminalHarness.handlers.current?.onExit?.(0 as never);
    });

    expect(screen.getByTestId("pane-status-Dana").dataset.status).toBe("exited");
    const notice = screen.getByTestId("pane-notice-Dana");
    expect(notice.dataset.tone).toBe("warning");
    // The exit reason is a CLAUSE ("stopped"), so the notice puts the agent in
    // front of it rather than showing a strip that reads as one bare word.
    expect(notice.textContent).toContain("Claude Code stopped");

    fireEvent.click(screen.getByTestId("pane-restart-Dana"));
    expect(onRestart).toHaveBeenCalledTimes(1);
  });

  it("marks an unreachable pane as an error rather than a warning", () => {
    render(pane({ onRestart: () => undefined }));

    act(() => {
      terminalHarness.handlers.current?.onTrouble?.(
        "This pane could not be reached." as never,
        false as never,
      );
    });

    expect(screen.getByTestId("pane-status-Dana").dataset.status).toBe("error");
    const notice = screen.getByTestId("pane-notice-Dana");
    expect(notice.dataset.tone).toBe("error");
    expect(notice.textContent).toContain("could not be reached");
  });

  /*
   * The edge, read the way the browser reads it.
   *
   * jsdom re-serialises a colour the moment it is assigned, and it does not
   * agree with the source spelling about spaces — so the expected value is put
   * through the SAME assignment rather than compared as a string. Otherwise
   * this test is about `rgba(0,0,0,0.05)` versus `rgba(0, 0, 0, 0.05)`, which
   * is a fact about jsdom and not about the pane.
   */
  const asBorderColor = (value: string) => {
    const probe = document.createElement("div");
    probe.style.borderColor = value;
    return probe.style.borderColor;
  };

  /**
   * The edge is what a reader can SWEEP — the badge and the notice both have to
   * be landed on first. A pane whose agent is gone recedes; one that failed
   * carries the terminal's own red.
   */
  it("carries the pane's lifecycle in its edge", () => {
    render(pane({ onRestart: () => undefined }));
    const frame = screen.getByTestId("agentic-pane-Dana");
    const resting = frame.style.borderColor;

    expect(resting).toBe(asBorderColor(PANE_CHROME.dark.edge.connecting));

    act(() => {
      terminalHarness.handlers.current?.onExit?.(0 as never);
    });
    expect(frame.style.borderColor).toBe(
      asBorderColor(PANE_CHROME.dark.edge.exited),
    );
    // Dimmer than resting rather than another colour — a finished terminal is
    // not a problem, so it steps back instead of announcing itself.
    expect(frame.style.borderColor).not.toBe(resting);

    act(() => {
      terminalHarness.handlers.current?.onTrouble?.(
        "This pane could not be reached." as never,
        false as never,
      );
    });
    expect(frame.style.borderColor).toBe(
      asBorderColor(PANE_CHROME.dark.edge.error),
    );
  });

  /**
   * The three accent states paint the edge through a CLASS, and an inline
   * colour beats every class. So this is not a style preference — leaving the
   * property set is what stopped a dragged pane, and one that had just been
   * handed a prompt, from showing anything but the shadow half of its own
   * highlight.
   */
  it("lets the accent states own the edge outright", () => {
    const view = render(pane({ focused: true }));
    const frame = screen.getByTestId("agentic-pane-Dana");

    expect(frame.style.borderColor).toBe("");
    expect(frame.className).toContain("border-primary/60");

    view.rerender(pane({ focused: false }));
    expect(frame.style.borderColor).not.toBe("");

    // A prompt just landed: two seconds of ring, edge included.
    act(() => {
      terminalHarness.handlers.current?.onPrompt?.(
        { text: "Run the tests", at: 2, chars: 13 } as never,
      );
    });
    expect(frame.style.borderColor).toBe("");
  });

  /**
   * A scheduled retry is not a dead pane. Calling it an error there is what
   * painted a whole grid red over a backend that was merely restarting — the
   * notice must stand down with it.
   */
  it("takes the notice away again while the socket is only retrying", () => {
    render(pane());

    act(() => {
      terminalHarness.handlers.current?.onTrouble?.(
        "Reconnecting…" as never,
        true as never,
      );
    });

    expect(screen.getByTestId("pane-status-Dana").dataset.status).toBe(
      "connecting",
    );
    expect(screen.queryByTestId("pane-notice-Dana")).toBeNull();
  });
});
