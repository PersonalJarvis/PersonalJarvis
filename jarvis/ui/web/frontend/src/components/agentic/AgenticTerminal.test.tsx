import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const terminalHarness = vi.hoisted(() => ({
  open: vi.fn(),
  observe: vi.fn(),
  fit: vi.fn(),
  /** What the terminal reports after a fit — a test moves it to grow the pane. */
  size: { cols: 80, rows: 24 },
  /** Every frame the pane hands its socket. Returns whether it went out. */
  send: vi.fn<(payload: unknown) => boolean>(() => true),
  /** The live socket's handlers, so a test can play a reconnect. */
  handlers: { current: null as Record<string, (...args: never[]) => void> | null },
  /** Everything the pane types into the terminal on the user's behalf. */
  input: vi.fn<(data: string) => void>(),
  /** xterm's single custom key handler, so a test can press a key. */
  keys: { current: null as ((event: KeyboardEvent) => boolean) | null },
}));

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    get cols() {
      return terminalHarness.size.cols;
    }
    get rows() {
      return terminalHarness.size.rows;
    }
    options: Record<string, unknown> = {};
    unicode = { activeVersion: "" };
    // The pane silences xterm's own answers to the agent's protocol queries
    // (see ./terminalQueries). The double only has to accept the handlers.
    parser = {
      registerOscHandler: () => ({ dispose() {} }),
      registerCsiHandler: () => ({ dispose() {} }),
    };

    loadAddon() {}
    open(host: HTMLElement) {
      terminalHarness.open(host);
    }
    focus() {}
    paste() {}
    attachCustomKeyEventHandler(handler: (event: KeyboardEvent) => boolean) {
      terminalHarness.keys.current = handler;
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
    write() {}
    resize() {}
    dispose() {}
    clearTextureAtlas() {}
  },
}));

vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit() {
      terminalHarness.fit();
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

import { AgenticTerminal } from "./AgenticTerminal";

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
    globalThis.ResizeObserver = ResizeObserverHarness;
  });

  afterEach(() => {
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
    expect(viewport?.className).toContain("px-2");
    expect(viewport?.className).toContain("pb-1");
    expect(viewport?.className).toContain("pt-1");
    expect(host.className).toContain("h-full");
    expect(host.className).toContain("min-h-0");
    expect(host.className).not.toMatch(/(?:^|\s)p[trblxy]?-/);
    expect(terminalHarness.open).toHaveBeenCalledWith(host);
    expect(terminalHarness.observe).toHaveBeenCalledWith(host);
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

  it("offers the longer recap in a tooltip the clipped line points at", () => {
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
    const tooltip = screen.getByTestId("pane-recap-tooltip-Dana");

    expect(tooltip.textContent).toContain("Fix the failing login test");
    // Which CLI runs here stays one hover away rather than being lost.
    expect(tooltip.textContent).toContain("Claude Code");
    expect(tooltip.getAttribute("role")).toBe("tooltip");
    // The clipped line names its own explanation, so a screen reader reaches
    // the full sentence the same way a mouse does.
    expect(line.getAttribute("aria-describedby")).toBe(tooltip.id);
    expect(line.getAttribute("tabindex")).toBe("0");
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
    expect(screen.queryByTestId("pane-recap-tooltip-Dana")).toBeNull();
  });

  it("adds no tooltip when the recap already says everything", () => {
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

    // jsdom measures no widths, so nothing is clipped here — with an identical
    // detail there is genuinely nothing more to reveal.
    expect(screen.queryByTestId("pane-recap-tooltip-Dana")).toBeNull();
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

  const pane = (maximized: boolean) => (
    <AgenticTerminal
      name="Dana"
      displayName="Claude Code"
      appearance="dark"
      fontSize={13}
      maximized={maximized}
    />
  );

  beforeEach(() => {
    vi.useFakeTimers();
    globalThis.ResizeObserver = ResizeObserverHarness;
    giveTheHostASize();
    terminalHarness.fit.mockClear();
    terminalHarness.send.mockClear();
    terminalHarness.send.mockImplementation(() => true);
    terminalHarness.handlers.current = null;
    terminalHarness.size = { cols: 80, rows: 24 };
  });

  afterEach(() => {
    vi.useRealTimers();
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
