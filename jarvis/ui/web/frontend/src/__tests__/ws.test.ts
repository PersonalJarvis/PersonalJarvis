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
});
