import { render } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BURST, resetConnectBudgetForTests } from "@/lib/connectBudget";

const sockets = vi.hoisted(() => ({ opened: 0 }));

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    cols = 80;
    rows = 24;
    options: Record<string, unknown> = {};
    loadAddon() {}
    open() {}
    write() {}
    focus() {}
    onData() { return { dispose() {} }; }
    dispose() {}
  },
}));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit() {} } }));
vi.mock("@xterm/addon-web-links", () => ({ WebLinksAddon: class {} }));
vi.mock("../agentic/terminalNewline", () => ({
  installNewlineBridge: () => () => undefined,
}));
vi.mock("@/hooks/useTheme", () => ({ useThemeValue: () => "dark" }));
vi.mock("@/lib/terminalFont", () => ({
  TERMINAL_FONT_STACK: "monospace",
  syncTerminalFont: () => () => undefined,
}));
vi.mock("@/lib/terminalLinks", () => ({
  activateTerminalLink: () => undefined,
  TERMINAL_OSC_LINK_HANDLER: {},
}));

import { WorkspaceTerminal } from "./WorkspaceTerminal";

class ResizeObserverHarness {
  observe() {}
  disconnect() {}
}

class WebSocketHarness {
  static OPEN = 1;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  constructor(_url: string) { sockets.opened += 1; }
  send() {}
  close() {}
}

describe("WorkspaceTerminal connection budget", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetConnectBudgetForTests();
    sockets.opened = 0;
    vi.stubGlobal("ResizeObserver", ResizeObserverHarness);
    vi.stubGlobal("WebSocket", WebSocketHarness);
  });

  afterEach(() => {
    resetConnectBudgetForTests();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("paces a burst of workspace terminal mounts", () => {
    const panes = Array.from({ length: 20 }, (_, index) =>
      render(<WorkspaceTerminal paneKey={`pane-${index}`} title={`Pane ${index}`} />),
    );
    act(() => vi.advanceTimersByTime(0));
    expect(sockets.opened).toBe(BURST);
    act(() => vi.advanceTimersByTime(5_000));
    expect(sockets.opened).toBe(20);
    panes.forEach((pane) => pane.unmount());
  });

  it("cancels queued connects when panes unmount", () => {
    const panes = Array.from({ length: 20 }, (_, index) =>
      render(<WorkspaceTerminal paneKey={`pane-${index}`} title={`Pane ${index}`} />),
    );
    act(() => vi.advanceTimersByTime(0));
    expect(sockets.opened).toBe(BURST);
    panes.forEach((pane) => pane.unmount());
    act(() => vi.advanceTimersByTime(5_000));
    expect(sockets.opened).toBe(BURST);
  });
});
