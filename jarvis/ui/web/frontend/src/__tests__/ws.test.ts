import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { WSClient } from "@/lib/ws";
import { WSEventEnvelope } from "@/schema/ws";

/** Minimal mock for WebSocket. */
class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  readyState = MockWebSocket.OPEN;
  static last: MockWebSocket | null = null;
  private listeners: Record<string, Array<(ev: any) => void>> = {};
  url: string;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.last = this;
    queueMicrotask(() => this.fire("open", {}));
  }

  addEventListener(type: string, fn: (ev: any) => void) {
    (this.listeners[type] ??= []).push(fn);
  }

  send = vi.fn();

  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
    this.fire("close", {});
  });

  fire(type: string, ev: any) {
    (this.listeners[type] ?? []).forEach((fn) => fn(ev));
  }

  deliver(data: unknown) {
    this.fire("message", { data: typeof data === "string" ? data : JSON.stringify(data) });
  }
}

describe("WSClient", () => {
  const OriginalWS = globalThis.WebSocket;

  beforeEach(() => {
    (globalThis as any).WebSocket = MockWebSocket;
    (globalThis as any).window = globalThis;
    (window as any).location = { protocol: "http:", host: "localhost:5173" };
  });

  afterEach(() => {
    (globalThis as any).WebSocket = OriginalWS;
    MockWebSocket.last = null;
  });

  it("invokes onMessage with parsed JSON envelope", async () => {
    const handler = vi.fn();
    const client = new WSClient({ onMessage: handler });
    client.connect();
    // let the queued microtask for "open" run
    await Promise.resolve();
    const envelope = {
      type: "event",
      event_name: "bus.ping",
      source_layer: "bus",
      timestamp_ns: Date.now() * 1_000_000,
      trace_id: "test-trace",
      payload: { hello: "world" },
    };
    MockWebSocket.last!.deliver(envelope);
    expect(handler).toHaveBeenCalledTimes(1);
    const received = handler.mock.calls[0][0];
    const parsed = WSEventEnvelope.safeParse(received);
    expect(parsed.success).toBe(true);
    client.close();
  });

  it("serialises non-string sends to JSON", async () => {
    const client = new WSClient();
    client.connect();
    await Promise.resolve();
    client.send({ type: "message", kind: "text", content: "hi" });
    expect(MockWebSocket.last!.send).toHaveBeenCalledWith(
      JSON.stringify({ type: "message", kind: "text", content: "hi" }),
    );
    client.close();
  });

  it("never places an injected session token in the socket URL", () => {
    window.__JARVIS_TOKEN = "session-secret";
    const client = new WSClient();
    client.connect();

    expect(MockWebSocket.last!.url).toBe("ws://localhost:5173/ws");
    expect(MockWebSocket.last!.url).not.toContain("session-secret");
    client.close();
  });

  it("passes the close code to onClose", async () => {
    const onClose = vi.fn();
    const client = new WSClient({ onClose });
    client.connect();
    await Promise.resolve();
    MockWebSocket.last!.fire("close", { code: 1013 });
    expect(onClose).toHaveBeenCalledWith(1013);
    client.close();
  });

  it("retries fast (no backoff escalation) after a 1013 warming close", async () => {
    vi.useFakeTimers();
    try {
      const client = new WSClient();
      client.connect();
      const first = MockWebSocket.last;
      // Warming close → must reconnect at MIN_BACKOFF (500ms), not escalate.
      first!.fire("close", { code: 1013 });
      vi.advanceTimersByTime(500);
      expect(MockWebSocket.last).not.toBe(first); // a new socket was opened
      client.close();
    } finally {
      vi.useRealTimers();
    }
  });

  // 4401 = the boundary rejected the handshake credential. WebKit engines do
  // not attach the HttpOnly session cookie to WS handshakes (BUG-065), so the
  // client must prove its session over plain HTTP and retry with the ticket.
  describe("4401 one-time-ticket retry (WebKit cookie-less handshake)", () => {
    const originalFetch = globalThis.fetch;

    afterEach(() => {
      (globalThis as any).fetch = originalFetch;
    });

    it("mints a ticket, reports authRetryPending, and reconnects with ?ticket=", async () => {
      vi.useFakeTimers();
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ ticket: "one-time-abc", expires_in: 60 }),
      });
      (globalThis as any).fetch = fetchMock;
      try {
        const onClose = vi.fn();
        const client = new WSClient({ onClose });
        client.connect();
        const first = MockWebSocket.last;

        first!.fire("close", { code: 4401 });
        await vi.advanceTimersByTimeAsync(500);

        expect(fetchMock).toHaveBeenCalledWith(
          "/api/ui/ws-ticket",
          expect.objectContaining({ method: "POST", credentials: "same-origin" }),
        );
        expect(onClose).toHaveBeenCalledWith(4401, { authRetryPending: true });
        expect(MockWebSocket.last).not.toBe(first);
        expect(MockWebSocket.last!.url).toBe(
          "ws://localhost:5173/ws?ticket=one-time-abc",
        );

        // The ticket is single-use: a later ordinary close reconnects bare.
        MockWebSocket.last!.fire("close", { code: 1013 });
        await vi.advanceTimersByTimeAsync(500);
        expect(MockWebSocket.last!.url).toBe("ws://localhost:5173/ws");
        client.close();
      } finally {
        vi.useRealTimers();
      }
    });

    it("stops fast-retrying after persistent 4401s and reports honest offline", async () => {
      vi.useFakeTimers();
      (globalThis as any).fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ ticket: "one-time-abc", expires_in: 60 }),
      });
      try {
        const onClose = vi.fn();
        const client = new WSClient({ onClose });
        client.connect();

        // Three consecutive 4401s stay within the fast-retry budget...
        for (let i = 0; i < 3; i += 1) {
          MockWebSocket.last!.fire("close", { code: 4401 });
          await vi.advanceTimersByTimeAsync(500);
          expect(onClose).toHaveBeenLastCalledWith(4401, { authRetryPending: true });
        }
        // ...the fourth means fresh tickets are not unlocking the socket:
        // report the honest offline state (escalating backoff takes over).
        MockWebSocket.last!.fire("close", { code: 4401 });
        await vi.advanceTimersByTimeAsync(0);
        expect(onClose).toHaveBeenLastCalledWith(4401, { authRetryPending: false });
        client.close();
      } finally {
        vi.useRealTimers();
      }
    });

    it("reports a dead session honestly when the mint fails", async () => {
      vi.useFakeTimers();
      (globalThis as any).fetch = vi.fn().mockResolvedValue({ ok: false });
      try {
        const onClose = vi.fn();
        const client = new WSClient({ onClose });
        client.connect();
        const first = MockWebSocket.last;

        first!.fire("close", { code: 4401 });
        await vi.advanceTimersByTimeAsync(500);

        expect(onClose).toHaveBeenCalledWith(4401, { authRetryPending: false });
        // Falls back to the normal reconnect path, without a ticket.
        expect(MockWebSocket.last).not.toBe(first);
        expect(MockWebSocket.last!.url).toBe("ws://localhost:5173/ws");
        client.close();
      } finally {
        vi.useRealTimers();
      }
    });
  });
});
