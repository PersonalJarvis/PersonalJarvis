import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useBrowserView } from "./useBrowserView";
import { BROWSER_PROFILE_CHANGED_EVENT } from "@/lib/browserProfiles";

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

test("inline login is capability gated and never buffers input across browser replacement", async () => {
  class ReadyImage {
    width = 1280; height = 800;
    onload?: () => void;
    set src(_value: string) { this.onload?.(); }
  }
  vi.stubGlobal("Image", ReadyImage);
  const hook = await mount();
  const canvas = document.createElement("canvas");
  vi.spyOn(canvas, "getContext").mockReturnValue({ drawImage: vi.fn() } as unknown as CanvasRenderingContext2D);
  Object.defineProperty(hook.result.current.canvas, "current", { value: canvas, writable: true });
  expect(hook.result.current.state.loginAvailable).toBe(false);
  act(() => Socket.current.onmessage?.({ data: JSON.stringify({ kind: "state", generation: "old",
    manual: false, login_available: true, login_mode: false, tabs: [] }) }));
  expect(hook.result.current.state.loginAvailable).toBe(true);
  act(() => {
    hook.result.current.control("takeover", { enabled: true, login: true });
    Socket.current.onmessage?.({ data: JSON.stringify({ kind: "control", ok: true }) });
    hook.result.current.control("text", { text: "must not reach another browser" });
    Socket.current.onmessage?.({ data: JSON.stringify({ kind: "state", generation: "login",
      manual: true, login_available: true, login_mode: true, tabs: [] }) });
    Socket.current.onmessage?.({ data: JSON.stringify({ kind: "control", ok: true, manual: true, login_mode: true }) });
  });
  expect(hook.result.current.state.loginMode).toBe(true);
  expect(hook.result.current.state.previewPaused).toBe(false);
  expect(Socket.current.send.mock.calls.map(([value]) => JSON.parse(value))).toEqual([
    { op: "takeover", args: { enabled: true, login: true } },
  ]);
  act(() => {
    Socket.current.onmessage?.({ data: JSON.stringify({ kind: "frame", data: "fixture", generation: "login", sequence: 1, timestamp: Date.now() / 1000 }) });
    hook.result.current.control("text", { text: "human input" });
  });
  expect(Socket.current.send).toHaveBeenLastCalledWith(JSON.stringify({ op: "text", args: { text: "human input", generation: "login" } }));
});

test("explicit handback blocks input until its acknowledgement", async () => {
  const hook = await mount();
  act(() => Socket.current.onmessage?.({ data: JSON.stringify({ kind: "control", ok: true, manual: true, login_mode: true }) }));
  act(() => {
    hook.result.current.control("takeover", { enabled: false, login: false });
    hook.result.current.control("click", { x: 20, y: 30 });
  });
  expect(Socket.current.send.mock.calls.map(([value]) => JSON.parse(value))).toEqual([
    { op: "takeover", args: { enabled: false, login: false } },
  ]);
});

test("an early sign-in denial permits retry without reconnecting", async () => {
  const hook = await mount();
  act(() => hook.result.current.control("takeover", { enabled: true, login: true }));
  act(() => Socket.current.onmessage?.({ data: JSON.stringify({
    kind: "control", op: "takeover", ok: false, error: "Controlled by another viewer",
  }) }));
  expect(hook.result.current.state.controlPending).toBe(false);
  act(() => hook.result.current.control("takeover", { enabled: true, login: true }));
  expect(Socket.current.send).toHaveBeenCalledTimes(2);
  expect(Socket.current.close).not.toHaveBeenCalled();
});

test("silent live connection becomes disconnected and pays the reconnect budget", async () => {
  const hook = await mount();
  expect(hook.result.current.state.connected).toBe(true);
  act(() => vi.advanceTimersByTime(8000));
  expect(hook.result.current.state.connected).toBe(false);
  expect(Socket.current.close).toHaveBeenCalledOnce();
  expect(connect).toHaveBeenCalledTimes(2);
});

test("extended native input requires an explicit worker capability and resets on reconnect", async () => {
  const hook = await mount();
  act(() => Socket.current.onmessage?.({ data: JSON.stringify({ kind: "state", manual: true, full_window: true, url: "", tabs: [] }) }));
  expect(hook.result.current.state.extendedInput).toBe(false);
  act(() => Socket.current.onmessage?.({ data: JSON.stringify({ kind: "state", manual: true, full_window: true, extended_input: true, url: "", tabs: [] }) }));
  expect(hook.result.current.state.extendedInput).toBe(true);
  act(() => Socket.current.close());
  expect(hook.result.current.state.extendedInput).toBe(false);
});


test("disconnect discards buffered frames before an old image decoder can restore input capabilities", async () => {
  class FrameImage {
    static pending: FrameImage[] = [];
    width = 1280; height = 800;
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor() { FrameImage.pending.push(this); }
    set src(_value: string) { /* Image completion is controlled by this test. */ }
  }
  vi.stubGlobal("Image", FrameImage);
  const hook = await mount();
  const canvas = document.createElement("canvas");
  const drawImage = vi.fn();
  vi.spyOn(canvas, "getContext").mockReturnValue({ drawImage } as unknown as CanvasRenderingContext2D);
  Object.defineProperty(hook.result.current.canvas, "current", { value: canvas, writable: true });
  for (const sequence of [1, 2]) {
    act(() => Socket.current.onmessage?.({ data: JSON.stringify({ kind: "frame", data: "fixture",
      sequence, timestamp: Date.now() / 1000, generation: "old", full_window: true, extended_input: true }) }));
  }
  expect(FrameImage.pending).toHaveLength(1);
  act(() => Socket.current.close());
  act(() => FrameImage.pending[0].onload?.());
  expect(FrameImage.pending).toHaveLength(1);
  expect(drawImage).not.toHaveBeenCalled();
  expect(hook.result.current.state.ready).toBe(false);
  expect(hook.result.current.state.extendedInput).toBe(false);
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

test("changing a profile drops stale control input and reconnects through the shared budget", async () => {
  const hook = await mount();
  const previous = Socket.current;
  act(() => hook.result.current.control("text", { text: "old profile input" }));
  act(() => window.dispatchEvent(new CustomEvent(BROWSER_PROFILE_CHANGED_EVENT, { detail: { agentIds: ["test"] } })));
  expect(previous.close).toHaveBeenCalledOnce();
  expect(hook.result.current.state.connected).toBe(false);
  expect(hook.result.current.state.controlPending).toBe(false);
  expect(connect).toHaveBeenCalledTimes(2);
  await act(async () => { connect.mock.calls[1][0](); });
  act(() => Socket.current.onmessage?.({ data: JSON.stringify({ kind: "control", ok: true, manual: true }) }));
  expect(Socket.current.send).not.toHaveBeenCalled();
});

test("another agent profile change leaves the current browser alone", async () => {
  await mount();
  act(() => window.dispatchEvent(new CustomEvent(BROWSER_PROFILE_CHANGED_EVENT, { detail: { agentIds: ["other"] } })));
  expect(Socket.current.close).not.toHaveBeenCalled();
  expect(connect).toHaveBeenCalledTimes(1);
});

test("manual Chrome login pauses frames and mirrored input until control is returned", async () => {
  const image = vi.fn();
  vi.stubGlobal("Image", image);
  const hook = await mount();
  act(() => Socket.current.onmessage?.({ data: JSON.stringify({ kind: "state", preview_paused: true, manual: true, tabs: [] }) }));
  expect(hook.result.current.state.previewPaused).toBe(true);
  expect(hook.result.current.state.ready).toBe(false);
  act(() => {
    Socket.current.onmessage?.({ data: JSON.stringify({ kind: "frame", data: "private-frame", sequence: 1, timestamp: Date.now() / 1000 }) });
    hook.result.current.control("text", { text: "private login" });
  });
  expect(image).not.toHaveBeenCalled();
  expect(Socket.current.send).not.toHaveBeenCalled();
  for (let n = 0; n < 3; n++) act(() => {
    vi.advanceTimersByTime(4000);
    Socket.current.onmessage?.({ data: JSON.stringify({ kind: "state", preview_paused: true, manual: true, tabs: [] }) });
  });
  expect(Socket.current.close).not.toHaveBeenCalled();
  act(() => hook.result.current.control("takeover", { enabled: false }));
  expect(Socket.current.send).toHaveBeenCalledWith(JSON.stringify({ op: "takeover", args: { enabled: false } }));
});
