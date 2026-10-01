import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { WorkspaceTerminal } from "./WorkspaceTerminal";
import { resetConnectBudgetForTests } from "@/lib/connectBudget";

const terminal = vi.hoisted(() => ({ focus: vi.fn(), dispose: vi.fn(), fit: vi.fn() }));
vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    cols = 80; rows = 24; options = {};
    open() {} loadAddon() {} write() {} onData() {}
    focus = terminal.focus;
    dispose = terminal.dispose;
  },
}));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit = terminal.fit; } }));
vi.mock("@xterm/addon-web-links", () => ({ WebLinksAddon: class {} }));
vi.mock("../agentic/terminalNewline", () => ({ installNewlineBridge: () => () => {} }));
vi.mock("@/lib/terminalFont", () => ({ TERMINAL_FONT_STACK: "monospace", syncTerminalFont: () => () => {} }));

class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 1;
  onopen?: () => void;
  onmessage?: (event: { data: string }) => void;
  onclose?: (event: { code: number }) => void;
  close = vi.fn();
  send = vi.fn();
  constructor(readonly url: string) { Socket.instances.push(this); }
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  resetConnectBudgetForTests();
  Socket.instances = [];
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); resetConnectBudgetForTests(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it("pins the shell socket and retains it across tab visibility and appearance changes", () => {
  const props = { paneKey: "terminal-one", agentName: "shell", workspaceId: "workspace-one", title: "Terminal 1" };
  const { rerender, unmount } = render(<WorkspaceTerminal {...props} active appearance="dark" />);
  act(() => vi.runOnlyPendingTimers());
  const socket = Socket.instances[0];
  const url = new URL(socket.url);
  expect(url.searchParams.get("agent")).toBe("shell");
  expect(url.searchParams.get("workspace_id")).toBe("workspace-one");
  rerender(<WorkspaceTerminal {...props} active={false} appearance="light" />);
  terminal.focus.mockClear();
  act(() => socket.onmessage?.({ data: JSON.stringify({ t: "ready" }) }));
  expect(terminal.focus).not.toHaveBeenCalled();
  rerender(<WorkspaceTerminal {...props} active appearance="light" />);
  act(() => vi.runOnlyPendingTimers());
  expect(Socket.instances).toHaveLength(1);
  expect(socket.close).not.toHaveBeenCalled();
  expect(terminal.focus).toHaveBeenCalled();
  unmount();
  expect(socket.close).toHaveBeenCalledOnce();
  expect(terminal.dispose).toHaveBeenCalledOnce();
});

it("cancels a queued connection when its tab closes before the budget grants it", () => {
  const { unmount } = render(<WorkspaceTerminal paneKey="one" agentName="shell" workspaceId="workspace-one" title="Terminal" />);
  unmount();
  act(() => vi.runOnlyPendingTimers());
  expect(Socket.instances).toHaveLength(0);
});
