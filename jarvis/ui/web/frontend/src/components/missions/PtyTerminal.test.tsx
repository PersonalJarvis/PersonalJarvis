import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PtyTerminal } from "./PtyTerminal";

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    cols = 80;
    rows = 24;
    loadAddon() {}
    open() {}
    write(_data: string, done?: () => void) { done?.(); }
    dispose() {}
  },
}));
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class { fit() {} },
}));
vi.mock("@xterm/addon-web-links", () => ({
  WebLinksAddon: class {},
}));
vi.mock("@xterm/addon-search", () => ({
  SearchAddon: class {},
}));

class MockWebSocket {
  static OPEN = 1;
  static last: MockWebSocket | null = null;
  readonly url: string;
  readyState = MockWebSocket.OPEN;
  binaryType: BinaryType = "blob";
  send = vi.fn();
  close = vi.fn();
  private listeners: Record<string, Array<(event: Event) => void>> = {};

  constructor(url: string | URL) {
    this.url = String(url);
    MockWebSocket.last = this;
  }

  addEventListener(type: string, listener: (event: Event) => void) {
    (this.listeners[type] ??= []).push(listener);
  }

  fire(type: string, event: Event) {
    for (const listener of this.listeners[type] ?? []) listener(event);
  }
}

describe("PtyTerminal mission authorization", () => {
  beforeEach(() => {
    MockWebSocket.last = null;
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ token: "pty-secret" }),
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("opens a token-free URL and sends the token as the first hello frame", async () => {
    render(<PtyTerminal workerId="worker/one" />);
    await waitFor(() => expect(MockWebSocket.last).not.toBeNull());

    const socket = MockWebSocket.last!;
    expect(socket.url).toContain("/api/missions/pty/worker%2Fone");
    expect(socket.url).not.toContain("pty-secret");
    expect(new URL(socket.url).search).toBe("");

    act(() => socket.fire("open", new Event("open")));
    expect(socket.send).toHaveBeenCalledTimes(1);
    expect(socket.send).toHaveBeenCalledWith(
      JSON.stringify({ type: "hello", token: "pty-secret" }),
    );
  });
});
