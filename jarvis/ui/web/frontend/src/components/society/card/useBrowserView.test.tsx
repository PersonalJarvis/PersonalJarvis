import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useBrowserView } from "./useBrowserView";

const { connect, ticket } = vi.hoisted(() => ({ connect: vi.fn(), ticket: vi.fn(async () => "fresh-ticket") }));
vi.mock("@/lib/ws", () => ({ mintWsTicket: ticket }));
vi.mock("@/lib/connectBudget", () => ({
  requestConnect: connect, jitteredDelay: () => 1000,
}));

class Socket {
  static OPEN = 1;
  static current: Socket;
  readyState = 1;
  onopen?: () => void;
  onclose?: (event: { code: number }) => void;
  onmessage?: (event: { data: string }) => void;
  send = vi.fn();
  close = vi.fn(() => { this.readyState = 3; this.onclose?.({ code: 1000 }); });
  constructor(public url: string) { Socket.current = this; }
}

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); connect.mockReset(); ticket.mockClear(); });

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

test("state heartbeats without first pixels recover instead of connecting forever", async () => {
  const hook = await mount();
  for (let n = 0; n < 4; n++) {
    act(() => {
      vi.advanceTimersByTime(4000);
      Socket.current.onmessage?.({ data: JSON.stringify({ kind: "state", url: "about:blank", tabs: [] }) });
    });
  }
  expect(hook.result.current.state.connected).toBe(false);
  expect(Socket.current.close).toHaveBeenCalledOnce();
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
  expect(ticket).not.toHaveBeenCalled();
});

test("cookie rejection reconnects with a fresh ticket through the shared budget", async () => {
  await mount();
  act(() => Socket.current.onclose?.({ code: 4401 }));
  expect(connect).toHaveBeenCalledTimes(2);
  await act(async () => { connect.mock.calls[1][0](); });
  expect(ticket).toHaveBeenCalledOnce();
  expect(Socket.current.url).toContain("?ticket=fresh-ticket");
});
