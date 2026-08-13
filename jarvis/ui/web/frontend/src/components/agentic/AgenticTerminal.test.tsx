import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const terminalHarness = vi.hoisted(() => ({
  open: vi.fn(),
  host: { current: null as HTMLElement | null },
  observe: vi.fn(),
  fit: vi.fn(),
  /** What the terminal reports after a fit — a test moves it to grow the pane. */
  size: { cols: 80, rows: 24 },
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
    // would land on.
    proposeDimensions() {
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

import {
  AgenticTerminal,
  DRAG_REFIT_MS,
  REBUILD_QUIET_MS,
  RESIZE_PARSE_WAIT_MS,
} from "./AgenticTerminal";
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

  it("explains the bar's gestures in its own card after a settled hover", () => {
    vi.useFakeTimers();
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        onToggleMaximize={() => undefined}
        onArrangeStart={() => undefined}
        onRename={async () => true}
      />,
    );

    // A pass-through hover shows nothing; the card waits for a settled one.
    fireEvent.pointerOver(screen.getByTestId("pane-header-Dana"));
    expect(screen.queryByTestId("pane-header-tip-Dana")).toBeNull();

    act(() => {
      vi.advanceTimersByTime(600);
    });

    // The same single sentence the native `title` tooltip carried, now in
    // the pane's own branded card.
    const tip = screen.getByTestId("pane-header-tip-Dana");
    expect(tip.textContent).toContain("Drag Dana by this bar");
    expect(tip.textContent).toContain("Double-click to fill the workspace");
    expect(
      screen.getByTestId("pane-header-Dana").getAttribute("title"),
    ).toBeNull();

    // A press is an answer, not a question — the card leaves as a drag begins.
    fireEvent.pointerDown(screen.getByTestId("pane-header-Dana"));
    expect(screen.queryByTestId("pane-header-tip-Dana")).toBeNull();
    vi.useRealTimers();
  });

  it("yields the gesture card to the bar's own controls", () => {
    vi.useFakeTimers();
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        onToggleMaximize={() => undefined}
        onArrangeStart={() => undefined}
      />,
    );

    // Hovering a control is a question about THAT control — the bar's card
    // never opens on top of it.
    fireEvent.pointerOver(screen.getByTestId("pane-maximize-Dana"));
    act(() => {
      vi.advanceTimersByTime(600);
    });
    expect(screen.queryByTestId("pane-header-tip-Dana")).toBeNull();
    vi.useRealTimers();
  });

  it("opens the gesture card over the title line, where the old tooltip lived", () => {
    vi.useFakeTimers();
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        recap="Fix the failing login test"
        appearance="dark"
        fontSize={13}
        onToggleMaximize={() => undefined}
        onArrangeStart={() => undefined}
      />,
    );

    // The recap line is a button, but it spans nearly the whole bar — the
    // card must show there, not only on the bar's empty slivers.
    fireEvent.pointerOver(screen.getByTestId("pane-recap-Dana"));
    act(() => {
      vi.advanceTimersByTime(600);
    });

    const tip = screen.getByTestId("pane-header-tip-Dana");
    expect(tip.textContent).toContain("Drag Dana by this bar");
    vi.useRealTimers();
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

  const pane = (
    maximized: boolean,
    extra: Partial<React.ComponentProps<typeof AgenticTerminal>> = {},
    active = true,
  ) => (
    <AgenticTerminal
      name="Dana"
      displayName="Claude Code"
      appearance="dark"
      fontSize={13}
      maximized={maximized}
      active={active}
      {...extra}
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
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
    Reflect.deleteProperty(HTMLElement.prototype, "clientHeight");
  });

  it("follows a drag in throttled steps instead of freezing until release", () => {
    // The reported jank (2026-08-11): while a seam moved, the text froze and
    // the release re-wrapped it in one hard snap. Mid-drag the pane now takes
    // at most one fit per DRAG_REFIT_MS — the burst of observer ticks below
    // collapses to a single refit, taken while the drag is still going.
    const view = render(pane(false, { layoutBusy: true }));
    settle();
    terminalHarness.fit.mockClear();

    act(() => {
      window.dispatchEvent(new Event("resize"));
      vi.advanceTimersByTime(50);
      window.dispatchEvent(new Event("resize"));
      vi.advanceTimersByTime(50);
      window.dispatchEvent(new Event("resize"));
    });
    // The throttle window is still open — nothing has refitted yet.
    expect(terminalHarness.fit).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(DRAG_REFIT_MS + 40);
    });
    // One fit for the whole burst, not one per tick.
    expect(terminalHarness.fit).toHaveBeenCalledTimes(1);

    // Letting go still lands the exact final size in its own immediate pass.
    terminalHarness.fit.mockClear();
    view.rerender(pane(false, { layoutBusy: false }));
    settle();
    expect(terminalHarness.fit).toHaveBeenCalled();
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
    const view = render(pane(false, {}, false));
    settle();
    terminalHarness.send.mockClear();

    view.rerender(pane(false, {}, true));
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

  /*
   * A terminal is exactly as wide as the tile it is shown in.
   *
   * The maintainer's rule for this screen (2026-08-11), and the reason the
   * tests it replaced are gone rather than adjusted. Those pinned the opposite:
   * a pane rendered a fixed 60-column grid whatever its tile could show and cut
   * the rest off at the edge, so six terminals each showed about two thirds of
   * themselves — which is what "the sessions overlap, the right one slides over
   * the left" was describing. It was working exactly as written.
   *
   * The width a coding CLI wants did not stop mattering; it stopped being
   * enforced HERE. The launcher warns from twenty terminals up and opens as
   * many as the user confirms, and a pane too narrow to be useful is now a pane
   * they can see is too narrow.
   */
  it("fits the tile it is shown in, down to the width its agent can draw in", () => {
    const view = render(pane(false));
    settle();
    terminalHarness.fit.mockClear();
    terminalHarness.send.mockClear();

    // Narrower than the pane opened at, and still a width a coding CLI lays a
    // frame out in. The tile's own measurement is what everyone gets.
    terminalHarness.size = { cols: 66, rows: 20 };
    view.rerender(pane(true));
    settle();

    expect(terminalHarness.fit).toHaveBeenCalled();
    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "r",
      cols: 66,
      rows: 20,
    });
  });

  /*
   * Below that width the pane keeps its agent's columns instead.
   *
   * This is the half of the rule that was missing until 2026-08-13, and the bug
   * it cost: opening five more terminals re-fits every pane already open, and
   * at thirteen columns a coding CLI's own repaint erases more of its screen
   * than it rewrites. Panes that had been working for an hour came back blank.
   *
   * The tile is still measured honestly and nothing is drawn past its edge —
   * there is simply no terminal in it to be wrong about. See PaneTooNarrowCard.
   */
  it("holds its agent's columns when the tile stops being drawable", () => {
    const view = render(pane(false));
    settle();
    terminalHarness.fit.mockClear();
    terminalHarness.send.mockClear();
    terminalHarness.resize.mockClear();

    // The crowded-grid measurement (~17 columns per cell, thirteen panes).
    terminalHarness.size = { cols: 17, rows: 6 };
    view.rerender(pane(true));
    settle();

    // Never fitted to the tile — fit() is what would have handed the agent 17.
    expect(terminalHarness.fit).not.toHaveBeenCalled();
    // The width the pane opened at, kept. Height still follows the tile: a
    // resize on that axis repaints in place and breaks nothing.
    expect(terminalHarness.resize).toHaveBeenCalledWith(80, 6);
    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "r",
      cols: 80,
      rows: 6,
    });
    expect(screen.getByTestId("pane-too-narrow-Dana")).toBeTruthy();
  });

  it("gives xterm and the agent the same number, always", () => {
    // The one thing that must never drift. An agent laying its lines out for a
    // width its xterm does not have re-wraps every one of them, and the TUI's
    // cursor moves then land on rows that hold something else — a five-pane
    // grid came back as panes full of shredded one-word fragments (2026-08-10).
    // Held columns are no exception: the pane pins xterm to exactly the number
    // it sends, which is why the terminal underneath a card is still correct.
    for (const size of [
      { cols: 17, rows: 6 },
      { cols: 33, rows: 40 },
      { cols: 90, rows: 30 },
    ]) {
      // Every pane in this loop OPENS with room, so the width it holds when a
      // narrow tile arrives is the same 80 in each case.
      terminalHarness.size = { cols: 80, rows: 24 };
      const view = render(pane(false));
      settle();
      terminalHarness.send.mockClear();
      terminalHarness.resize.mockClear();

      terminalHarness.size = size;
      view.rerender(pane(true));
      settle();

      // 80 is the width this pane opened at and therefore the one it holds.
      const shown = size.cols >= 60 ? size.cols : 80;
      expect(terminalHarness.send).toHaveBeenCalledWith({
        t: "r",
        cols: shown,
        rows: size.rows,
      });
      // fit() sizes xterm to the tile, so the agent hearing the tile's own
      // measurement IS the two agreeing; a held width says so explicitly.
      if (shown !== size.cols) {
        expect(terminalHarness.resize).toHaveBeenCalledWith(shown, size.rows);
      }
      view.unmount();
    }
  });

  it("refuses only a measurement that cannot be real", () => {
    // All the floor is for now: a tile mid-layout measures nothing, and a PTY
    // resized to zero columns permanently wrecks the agent's drawing.
    const view = render(pane(false));
    settle();
    terminalHarness.send.mockClear();

    terminalHarness.size = { cols: 0, rows: 0 };
    view.rerender(pane(true));
    settle();

    expect(terminalHarness.send).not.toHaveBeenCalledWith(
      expect.objectContaining({ cols: 0 }),
    );
  });

  it("keeps the reader's text size on a narrow tile", () => {
    // The auto-shrink that walked a narrow pane's text down until 60 columns
    // fit was rejected the day it shipped (2026-08-11): it silently overrode
    // the toolbar's size on every narrow pane, which read as the size controls
    // being dead. The pane draws at the READER'S size and takes the columns
    // that leaves it.
    const view = render(pane(false));
    settle();
    terminalHarness.send.mockClear();

    terminalHarness.size = { cols: 40, rows: 20 };
    view.rerender(pane(true));
    settle();

    const term =
      terminalHarness.instances[terminalHarness.instances.length - 1];
    expect(term?.options.fontSize).toBe(13);
    // The pane holds its agent's columns here rather than following the tile
    // (see "holds its agent's columns" above) — but it does it without touching
    // a single point of the reader's text size, which is what this pins.
    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "r",
      cols: 80,
      rows: 20,
    });
  });

  it("never offers a sideways scroll, at any width", () => {
    // A pane that can scroll sideways is a pane drawing past its own edge.
    // The horizontal window this used to open — scrollbar, two scroll shadows
    // — was an honest presentation of a thing that should not exist.
    const view = render(pane(false));
    settle();
    const host = screen.getByTestId("agentic-terminal-host-Dana");
    expect(host.className).toContain("overflow-hidden");
    expect(host.className).not.toContain("overflow-x-auto");

    terminalHarness.size = { cols: 12, rows: 5 };
    view.rerender(pane(true));
    settle();
    expect(host.className).toContain("overflow-hidden");
    expect(host.className).not.toContain("overflow-x-auto");
  });

  it("restores the reader's text size over one a stale build left behind", () => {
    // A pane shrunken by the rejected auto-shrink can survive into this build
    // through a rebuilt terminal or a long-lived window. The refit restates
    // the reader's choice before measuring, so the first resize puts it back.
    render(pane(false));
    settle();
    const term =
      terminalHarness.instances[terminalHarness.instances.length - 1];
    if (term) term.options.fontSize = 8;

    act(() => {
      window.dispatchEvent(new Event("resize"));
      vi.advanceTimersByTime(600);
    });

    expect(term?.options.fontSize).toBe(13);
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

/*
 * A pane may not change shape underneath a half-parsed screen.
 *
 * Resizing xterm REFLOWS its buffer, and an agent's TUI is drawn by relative
 * cursor moves — "up twelve rows, erase from here". A reflow landing between
 * two of xterm's parse slices moves the rows the rest of that stream is
 * addressing, so the erase lands on rows holding something else: the frame's
 * rule drawn twice, an answer printed under the copy it was replacing, or a
 * pane wiped down to its status line. Reported 2026-08-11 as terminals that
 * come out broken when they are moved around the workspace — which is exactly
 * when a pane's geometry changes while its agents are talking.
 */
describe("pane refit while the agent is drawing", () => {
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

  const pane = () => (
    <AgenticTerminal
      name="Dana"
      displayName="Claude Code"
      appearance="dark"
      fontSize={13}
    />
  );

  /** Run the pane's debounced fit request without reaching the parse deadline. */
  const askForARefit = () => {
    act(() => {
      window.dispatchEvent(new Event("resize"));
      vi.advanceTimersByTime(RESIZE_PARSE_WAIT_MS - 100);
    });
  };

  beforeEach(() => {
    vi.useFakeTimers();
    globalThis.ResizeObserver = ResizeObserverHarness;
    giveTheHostASize();
    terminalHarness.fit.mockClear();
    terminalHarness.resize.mockClear();
    terminalHarness.send.mockClear();
    terminalHarness.send.mockImplementation(() => true);
    terminalHarness.handlers.current = null;
    terminalHarness.size = { cols: 80, rows: 24 };
    terminalHarness.deferWrite = false;
    terminalHarness.writeCallbacks = [];
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    terminalHarness.deferWrite = false;
    terminalHarness.writeCallbacks = [];
    Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
    Reflect.deleteProperty(HTMLElement.prototype, "clientHeight");
  });

  /** Hand the pane output xterm has not finished parsing. */
  const startDrawing = () => {
    terminalHarness.deferWrite = true;
    act(() => {
      terminalHarness.handlers.current?.onOutput?.(
        "[12A[Jredrawing the frame" as never,
      );
    });
  };

  /** Let xterm finish parsing what it was handed. */
  const finishDrawing = () => {
    act(() => {
      for (const done of terminalHarness.writeCallbacks.splice(0)) done();
    });
  };

  it("holds the fit back while xterm is still parsing", () => {
    render(pane());
    act(() => {
      vi.advanceTimersByTime(600);
    });
    startDrawing();
    terminalHarness.fit.mockClear();
    terminalHarness.send.mockClear();

    terminalHarness.size = { cols: 40, rows: 12 };
    askForARefit();

    // Nothing has moved: the half-parsed screen keeps the grid it was drawn
    // into until it is finished with it.
    expect(terminalHarness.fit).not.toHaveBeenCalled();
    expect(terminalHarness.resize).not.toHaveBeenCalled();
    expect(terminalHarness.send).not.toHaveBeenCalled();
  });

  it("takes the new size the moment the parser reaches a gap", () => {
    render(pane());
    act(() => {
      vi.advanceTimersByTime(600);
    });
    startDrawing();
    terminalHarness.fit.mockClear();
    terminalHarness.send.mockClear();

    // A width the agent can still draw in, so this measures the parse gate and
    // nothing else — a narrow tile takes the held-columns path instead.
    terminalHarness.size = { cols: 64, rows: 12 };
    askForARefit();
    finishDrawing();

    expect(terminalHarness.fit).toHaveBeenCalled();
    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "r",
      cols: 64,
      rows: 12,
    });
  });

  it("fits anyway when the pane never stops talking", () => {
    // The wait is bounded on purpose: an agent midway through streaming an
    // answer offers no gap at all, and a terminal that never follows its tile
    // is worse than one frame parsed across a reflow — the agent would go on
    // formatting for a size no window is showing.
    render(pane());
    act(() => {
      vi.advanceTimersByTime(600);
    });
    startDrawing();
    terminalHarness.fit.mockClear();
    terminalHarness.send.mockClear();

    terminalHarness.size = { cols: 64, rows: 12 };
    askForARefit();
    act(() => {
      vi.advanceTimersByTime(RESIZE_PARSE_WAIT_MS + 50);
    });

    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "r",
      cols: 64,
      rows: 12,
    });
  });

  /*
   * A pane too narrow for its agent stops being a terminal and says what it is.
   *
   * The rule that a pane is exactly as wide as its tile is not in question
   * here — none of this draws a column past a tile edge. What it closes is the
   * gap the rule left open: a coding CLI below its usable width does not draw a
   * small tidy frame, it lays its interface out one and two characters wide and
   * then repaints over rows that no longer hold what it drew. The pane comes
   * back blank (reported 2026-08-13), and telling the user about it — which is
   * all this used to do — left the wreckage on screen.
   */
  it("shows what the agent is doing when the tile is too narrow to draw in", () => {
    terminalHarness.size = { cols: 22, rows: 30 };
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        activity="working"
        recap="Rewriting the prompt composer"
        onToggleMaximize={() => undefined}
      />,
    );
    act(() => {
      vi.advanceTimersByTime(600);
      // A pane still opening its socket reports "starting" whatever the backend
      // says about the agent — the same rule the header's pill follows.
      terminalHarness.handlers.current?.onReady?.({
        resumed: false,
        reattached: false,
        lastPrompt: null,
      } as never);
    });

    const card = screen.getByTestId("pane-too-narrow-Dana");
    expect(card.dataset.cols).toBe("22");
    // The state in a word, the recap, and the arithmetic behind the decision.
    expect(card.textContent).toContain("working");
    expect(card.textContent).toContain("Rewriting the prompt composer");
    expect(card.textContent).toContain("22 columns");
    // One sentence, not two: the header notice says the same thing and would be
    // on screen at the same time.
    expect(screen.queryByTestId("pane-width-notice-Dana")).toBeNull();
  });

  it("hands the pane back at its tile's width when the reader insists", () => {
    // A reader who wants to watch a 22-column terminal is not somebody this
    // should argue with. "Show it anyway" restores exactly the behaviour a
    // narrow pane had before the card existed — the tile's own width, handed to
    // the agent, plus the notice saying what that costs.
    terminalHarness.size = { cols: 22, rows: 30 };
    render(pane());
    act(() => {
      vi.advanceTimersByTime(600);
    });
    terminalHarness.send.mockClear();

    act(() => {
      screen.getByTestId("pane-too-narrow-anyway-Dana").click();
    });

    expect(screen.queryByTestId("pane-too-narrow-Dana")).toBeNull();
    expect(screen.getByTestId("pane-width-notice-Dana").dataset.cols).toBe("22");
    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "r",
      cols: 22,
      rows: 30,
    });

    // Narrower still, and still the reader's call.
    terminalHarness.size = { cols: 14, rows: 30 };
    act(() => {
      window.dispatchEvent(new Event("resize"));
      vi.advanceTimersByTime(600);
    });
    expect(screen.queryByTestId("pane-too-narrow-Dana")).toBeNull();
  });

  it("takes the card back once a widened pane is crowded a second time", () => {
    terminalHarness.size = { cols: 22, rows: 30 };
    render(pane());
    act(() => {
      vi.advanceTimersByTime(600);
    });
    act(() => {
      screen.getByTestId("pane-too-narrow-anyway-Dana").click();
    });

    // Given room…
    terminalHarness.size = { cols: 120, rows: 30 };
    act(() => {
      window.dispatchEvent(new Event("resize"));
      vi.advanceTimersByTime(600);
    });
    // …and crowded again. "Show it anyway" answered a question about the pane
    // as it was then, not a standing waiver for the rest of its life.
    terminalHarness.size = { cols: 22, rows: 30 };
    act(() => {
      window.dispatchEvent(new Event("resize"));
      vi.advanceTimersByTime(600);
    });

    expect(screen.getByTestId("pane-too-narrow-Dana")).toBeTruthy();
  });

  it("stays quiet on a pane with room to work", () => {
    terminalHarness.size = { cols: 80, rows: 24 };
    render(pane());
    act(() => {
      vi.advanceTimersByTime(600);
    });

    expect(screen.queryByTestId("pane-width-notice-Dana")).toBeNull();
    expect(screen.queryByTestId("pane-too-narrow-Dana")).toBeNull();
  });

  it("gives the terminal back once the pane is given room", () => {
    terminalHarness.size = { cols: 22, rows: 30 };
    render(pane());
    act(() => {
      vi.advanceTimersByTime(600);
    });
    expect(screen.getByTestId("pane-too-narrow-Dana")).toBeTruthy();
    terminalHarness.send.mockClear();

    terminalHarness.size = { cols: 120, rows: 30 };
    act(() => {
      window.dispatchEvent(new Event("resize"));
      vi.advanceTimersByTime(600);
    });

    expect(screen.queryByTestId("pane-too-narrow-Dana")).toBeNull();
    expect(screen.queryByTestId("pane-width-notice-Dana")).toBeNull();
    // And the agent is told about the room, which is what makes the terminal
    // underneath the card correct the moment it is uncovered.
    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "r",
      cols: 120,
      rows: 30,
    });
  });

  it("still fits a quiet pane in the same pass", () => {
    // The gate must cost nothing when there is nothing to wait for: a pane
    // whose agent is idle refits immediately, as it always did.
    render(pane());
    act(() => {
      vi.advanceTimersByTime(600);
    });
    terminalHarness.fit.mockClear();
    terminalHarness.send.mockClear();

    terminalHarness.size = { cols: 64, rows: 12 };
    askForARefit();

    expect(terminalHarness.fit).toHaveBeenCalled();
    expect(terminalHarness.send).toHaveBeenCalledWith({
      t: "r",
      cols: 64,
      rows: 12,
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

  it("lets a TUI canvas fill fall through to the glass instead of covering it", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
      />,
    );
    terminalHarness.write.mockClear();

    act(() => {
      terminalHarness.handlers.current?.onOutput?.(
        "\x1b[48;2;20;20;20m     \x1b[0m" as never,
      );
    });

    // GrokNight paints #141414 on every empty cell. That RGB is the canvas
    // fill; xterm must see the default background so the wallpaper shows.
    // The rewrite is not keyed on the pane's product name.
    expect(terminalHarness.write).toHaveBeenCalledWith("\x1b[49m     \x1b[0m");
  });

  it("clears GrokDay's light canvas the same way", () => {
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="light"
        fontSize={13}
      />,
    );
    terminalHarness.write.mockClear();

    act(() => {
      terminalHarness.handlers.current?.onOutput?.(
        "\x1b[48;2;238;238;238m     \x1b[0m" as never,
      );
    });

    expect(terminalHarness.write).toHaveBeenCalledWith("\x1b[49m     \x1b[0m");
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
