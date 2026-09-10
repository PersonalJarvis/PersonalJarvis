import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useBrowserView } from "./useBrowserView";

const { connect } = vi.hoisted(() => ({ connect: vi.fn() }));
vi.mock("@/lib/ws", () => ({ mintWsTicket: async () => "" }));
vi.mock("@/lib/connectBudget", () => ({
  requestConnect: connect, jitteredDelay: () => 1000,
}));

class Socket {
  static OPEN = 1;
  static current: Socket;
  readyState = 1;
  onopen?: () => void;
  onclose?: () => void;
  onmessage?: (event: { data: string }) => void;
  close = vi.fn(() => { this.readyState = 3; this.onclose?.(); });
  constructor() { Socket.current = this; }
}

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); connect.mockReset(); });

async function mount() {
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true })));
  connect.mockImplementation(() => () => {});
  connect.mockImplementationOnce((start: () => void) => { start(); return () => {}; });
  const hook = renderHook(() => useBrowserView("test"));
  await act(async () => {});
  act(() => Socket.current.onopen?.());
  return hook;
}

test("silent live connection becomes disconnected and pays the reconnect budget", async () => {
  const hook = await mount();
  expect(hook.result.current.state.connected).toBe(true);
  act(() => vi.advanceTimersByTime(8000));
  expect(hook.result.current.state.connected).toBe(false);
  expect(Socket.current.close).toHaveBeenCalledOnce();
  expect(connect).toHaveBeenCalledTimes(2);
});

test("static page heartbeats preserve a healthy connection without new images", async () => {
  const hook = await mount();
  for (let n = 0; n < 4; n++) {
    act(() => {
      vi.advanceTimersByTime(4000);
      Socket.current.onmessage?.({ data: JSON.stringify({ kind: "state", url: "about:blank", tabs: [] }) });
    });
  }
  expect(hook.result.current.state.connected).toBe(true);
  expect(Socket.current.close).not.toHaveBeenCalled();
});
