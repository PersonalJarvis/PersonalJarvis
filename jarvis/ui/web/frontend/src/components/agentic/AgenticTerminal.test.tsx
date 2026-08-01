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

  it("uses the only installed CLI instead of inheriting an unavailable one", () => {
    const onSplit = vi.fn();
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Claude Code"
        appearance="dark"
        fontSize={13}
        agents={[
          { ...CHOICES[0], installed: false },
          { ...CHOICES[1], installed: true },
        ]}
        onSplit={onSplit}
      />,
    );

    fireEvent.click(screen.getByTestId("pane-split-right-Dana"));

    expect(onSplit).toHaveBeenCalledWith("right", "codex");
  });

  it("retains pane inheritance while agent metadata is unavailable", () => {
    const onSplit = vi.fn();
    render(
      <AgenticTerminal
        name="Dana"
        displayName="Codex"
        appearance="dark"
        fontSize={13}
        onSplit={onSplit}
      />,
    );

    fireEvent.click(screen.getByTestId("pane-split-right-Dana"));

    expect(onSplit).toHaveBeenCalledWith("right", undefined);
  });

  it("associates a multiword pane's trigger with a DOM-safe dialog id", () => {
    render(
      <AgenticTerminal
        name="Frontend rewrite"
        displayName="Codex"
        appearance="dark"
        fontSize={13}
        agents={CHOICES}
        onSplit={() => undefined}
      />,
    );

    const trigger = screen.getByTestId("pane-split-right-Frontend rewrite");
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog");

    expect(trigger.getAttribute("aria-controls")).toBe(dialog.id);
    expect(dialog.id).not.toMatch(/\s/);
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
    const plain = screen.getByTestId("pane-split-right-Dana-shell");

    // Listed but unusable, so the absence explains itself instead of the entry
    // simply not being there — and in the terms of what is missing.
    expect(plain.getAttribute("aria-disabled")).toBe("true");
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
