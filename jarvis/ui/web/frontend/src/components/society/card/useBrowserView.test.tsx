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
  send = vi.fn();
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

test("cold browser startup stays connected until pixels are available", async () => {
  const hook = await mount();
  for (let n = 0; n < 50; n++) {
    act(() => {
      vi.advanceTimersByTime(2000);
      Socket.current.onmessage?.({ data: JSON.stringify({ kind: "starting" }) });
    });
  }
  expect(hook.result.current.state.connected).toBe(true);
  expect(hook.result.current.state.ready).toBe(false);
  expect(Socket.current.close).not.toHaveBeenCalled();
});

test("startup failure remains visible while reconnecting", async () => {
  const hook = await mount();
  act(() => {
    Socket.current.onmessage?.({ data: JSON.stringify({ kind: "error", error: "Browser startup failed" }) });
    Socket.current.close();
    Socket.current.onopen?.();
  });
  expect(hook.result.current.state.error).toBe("Browser startup failed");
  expect(hook.result.current.state.ready).toBe(false);
});

test("first click and typing wait for the exclusive control acknowledgement", async () => {
  const hook = await mount();
  act(() => {
    hook.result.current.control("click", { x: 240, y: 60 });
    hook.result.current.control("text", { text: "example.com" });
  });
  expect(Socket.current.send.mock.calls.map(([value]) => JSON.parse(value))).toEqual([
    { op: "takeover", args: { enabled: true } },
  ]);
  act(() => Socket.current.onmessage?.({ data: JSON.stringify({ kind: "control", ok: true, manual: true }) }));
  expect(Socket.current.send.mock.calls.map(([value]) => JSON.parse(value))).toEqual([
    { op: "takeover", args: { enabled: true } },
    { op: "click", args: { x: 240, y: 60 } },
    { op: "text", args: { text: "example.com" } },
  ]);
});

test("denied control never replays queued input", async () => {
  const hook = await mount();
  act(() => hook.result.current.control("text", { text: "private input" }));
  act(() => Socket.current.onmessage?.({ data: JSON.stringify({ kind: "control", ok: false, error: "Already controlled" }) }));
  act(() => Socket.current.onmessage?.({ data: JSON.stringify({ kind: "control", ok: true, manual: true }) }));
  expect(Socket.current.send).toHaveBeenCalledTimes(1);
});

test("connect does not wait for a separate HTTP setup request", async () => {
  await mount();
  expect(fetch).not.toHaveBeenCalled();
});
