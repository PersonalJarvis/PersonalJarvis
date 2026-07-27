import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { openPaneSocket } from "./paneSocket";

/** Minimal WebSocket stand-in, mirroring the one in __tests__/ws.test.ts. */
class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static last: MockWebSocket | null = null;
  static opened: MockWebSocket[] = [];

  readyState = MockWebSocket.OPEN;
  url: string;
  private listeners: Record<string, Array<(ev: unknown) => void>> = {};

  constructor(url: string) {
    this.url = url;
    MockWebSocket.last = this;
    MockWebSocket.opened.push(this);
  }

  addEventListener(type: string, fn: (ev: never) => void) {
    (this.listeners[type] ??= []).push(fn as (ev: unknown) => void);
  }

  send = vi.fn();

  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
  });

  fire(type: string, ev: unknown = {}) {
    (this.listeners[type] ?? []).forEach((fn) => fn(ev));
  }

  /** Server frame, as the pane protocol sends it. */
  deliver(payload: unknown) {
    this.fire("message", { data: JSON.stringify(payload) });
  }

  /** Close from the server side, with a code. */
  dropped(code: number) {
    this.readyState = MockWebSocket.CLOSED;
    this.fire("close", { code });
  }
}

function handlers() {
  return {
    onOpen: vi.fn(),
    onOutput: vi.fn(),
    onReady: vi.fn(),
    onExit: vi.fn(),
    onTrouble: vi.fn(),
  };
}

describe("openPaneSocket", () => {
  const originalWs = globalThis.WebSocket;
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    (globalThis as unknown as { WebSocket: unknown }).WebSocket = MockWebSocket;
    (globalThis as unknown as { window: unknown }).window = globalThis;
    (window as unknown as { location: unknown }).location = {
      protocol: "http:",
      host: "localhost:5173",
    };
    MockWebSocket.last = null;
    MockWebSocket.opened = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    (globalThis as unknown as { WebSocket: unknown }).WebSocket = originalWs;
    (globalThis as unknown as { fetch: unknown }).fetch = originalFetch;
  });

  it("addresses the pane by size and by the workspace holding it", () => {
    const socket = openPaneSocket(
      { name: "Mika", cols: 120, rows: 40, workspaceId: "ide_abc" },
      handlers(),
    );
    // The workspace id is not decoration: the front workspace can change while
    // this socket is alive, and the server resolves a pane without one against
    // whichever is showing — which is a different folder's pane.
    expect(MockWebSocket.last!.url).toBe(
      "ws://localhost:5173/api/agentic-ide/pty/Mika?cols=120&rows=40&workspace=ide_abc",
    );
    socket.close();
  });

  it("mints a one-time ticket and retries a rejected handshake (BUG-065)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ticket: "one-time-abc", expires_in: 60 }),
    });
    (globalThis as unknown as { fetch: unknown }).fetch = fetchMock;
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    const first = MockWebSocket.last;

    first!.dropped(4401);
    await vi.advanceTimersByTimeAsync(500);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ui/ws-ticket",
      expect.objectContaining({ method: "POST", credentials: "same-origin" }),
    );
    expect(MockWebSocket.last).not.toBe(first);
    expect(MockWebSocket.last!.url).toContain("ticket=one-time-abc");
    // A retry is under way, so the pane is not dead — saying so would put a
    // red "error" on a terminal that is about to work.
    expect(cb.onTrouble).toHaveBeenLastCalledWith(expect.any(String), true);
    socket.close();
  });

  it("gives up when the pane is no longer part of the open workspace", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Ghost", cols: 80, rows: 24 }, cb);
    const first = MockWebSocket.last;

    // 4404 is the server saying the pane does not exist here. Retrying cannot
    // change that answer, so the pane reports honestly and stops.
    first!.dropped(4404);
    await vi.advanceTimersByTimeAsync(10_000);

    expect(MockWebSocket.opened).toHaveLength(1);
    expect(cb.onTrouble).toHaveBeenLastCalledWith(expect.any(String), false);
    socket.close();
  });

  it("re-joins a running agent after the connection drops", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    MockWebSocket.last!.fire("open");
    MockWebSocket.last!.deliver({ t: "ready", resumed: false, reattached: false });
    expect(cb.onReady).toHaveBeenCalledTimes(1);

    // The agent outlives its viewer by design — the server keeps the PTY and
    // replays the screen to the next one. A dropped socket therefore means
    // "reconnect", not "the agent died".
    MockWebSocket.last!.dropped(1006);
    await vi.advanceTimersByTimeAsync(500);

    expect(MockWebSocket.opened).toHaveLength(2);
    expect(cb.onExit).not.toHaveBeenCalled();
    socket.close();
  });

  it("slows down once the attempt budget is spent, and says so", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);

    for (let i = 0; i < 12; i += 1) {
      MockWebSocket.last!.dropped(1006);
      await vi.advanceTimersByTimeAsync(20_000);
    }

    // Bounded: a backend that is genuinely gone must not be hammered, and the
    // pane must end up saying so rather than spinning silently.
    expect(cb.onTrouble).toHaveBeenLastCalledWith(expect.any(String), false);
    const attempts = MockWebSocket.opened.length;
    await vi.advanceTimersByTimeAsync(20_000);
    expect(MockWebSocket.opened).toHaveLength(attempts);
    socket.close();
  });

  it("keeps knocking every half minute instead of dying for the session", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);

    for (let i = 0; i < 12; i += 1) {
      MockWebSocket.last!.dropped(1006);
      await vi.advanceTimersByTimeAsync(20_000);
    }
    expect(cb.onTrouble).toHaveBeenLastCalledWith(expect.any(String), false);
    MockWebSocket.last!.dropped(1006);
    const spent = MockWebSocket.opened.length;

    // The reasons a pane cannot connect are nearly always temporary — an app
    // restarting, a machine waking up. A pane that gave up for good left a live
    // agent behind an unusable terminal until the whole workspace was rebuilt
    // by hand (BUG-113), so it stays quiet but never stops trying.
    await vi.advanceTimersByTimeAsync(5_000);
    expect(MockWebSocket.opened).toHaveLength(spent);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(MockWebSocket.opened.length).toBeGreaterThan(spent);

    MockWebSocket.last!.fire("open");
    MockWebSocket.last!.deliver({ t: "ready", resumed: false, reattached: true });
    expect(cb.onReady).toHaveBeenCalledTimes(1);
    socket.close();
  });

  it("waits out a backend that is up but has not restored the workspace yet", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);

    // 4503 is "not yet", the state every pane of a restored workspace connects
    // into for a second or two after the app restarts. Read as "no such pane"
    // it ended a whole grid at once; here it must cost nothing but patience.
    for (let i = 0; i < 12; i += 1) {
      MockWebSocket.last!.dropped(4503);
      await vi.advanceTimersByTimeAsync(30_000);
    }
    expect(cb.onTrouble).toHaveBeenLastCalledWith(expect.any(String), true);

    MockWebSocket.last!.fire("open");
    MockWebSocket.last!.deliver({ t: "ready", resumed: true, reattached: false });
    expect(cb.onReady).toHaveBeenCalledTimes(1);
    socket.close();
  });

  it("treats an agent exit as final, not as a dropped connection", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    MockWebSocket.last!.fire("open");
    MockWebSocket.last!.deliver({ t: "ready", resumed: true, reattached: false });
    MockWebSocket.last!.deliver({ t: "exit", code: 0 });

    MockWebSocket.last!.dropped(1000);
    await vi.advanceTimersByTimeAsync(20_000);

    expect(cb.onExit).toHaveBeenCalledWith(0);
    expect(MockWebSocket.opened).toHaveLength(1);
    socket.close();
  });

  it("does not reconnect after the pane closes its socket", async () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    socket.close();

    MockWebSocket.last!.dropped(1006);
    await vi.advanceTimersByTimeAsync(20_000);

    expect(MockWebSocket.opened).toHaveLength(1);
    expect(cb.onTrouble).not.toHaveBeenCalled();
  });

  it("passes output through and only sends while the socket is open", () => {
    const cb = handlers();
    const socket = openPaneSocket({ name: "Mika", cols: 80, rows: 24 }, cb);
    MockWebSocket.last!.fire("open");
    MockWebSocket.last!.deliver({ t: "o", d: "hello" });
    expect(cb.onOutput).toHaveBeenCalledWith("hello");

    socket.send({ t: "i", d: "ls\r" });
    expect(MockWebSocket.last!.send).toHaveBeenCalledWith(
      JSON.stringify({ t: "i", d: "ls\r" }),
    );

    MockWebSocket.last!.readyState = MockWebSocket.CLOSED;
    socket.send({ t: "i", d: "ignored" });
    expect(MockWebSocket.last!.send).toHaveBeenCalledTimes(1);
    socket.close();
  });
});
