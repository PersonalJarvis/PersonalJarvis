/**
 * The voice feature boots ~20s after the desktop window connects. The backend
 * announces readiness over the existing WS envelope as a `VoiceBootStatus`
 * event with `payload.ready: boolean`. This test drives the real
 * useWebSocket → WSClient → store path (via a MockWebSocket) and asserts that a
 * VoiceBootStatus frame flips `voiceReady` in the store.
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useWebSocket } from "@/hooks/useWebSocket";
import { useEventStore } from "@/store/events";

/** Minimal mock for WebSocket (mirrors __tests__/ws.test.ts). */
class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  readyState = MockWebSocket.OPEN;
  static last: MockWebSocket | null = null;
  private listeners: Record<string, Array<(ev: unknown) => void>> = {};
  url: string;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.last = this;
    queueMicrotask(() => this.fire("open", {}));
  }

  addEventListener(type: string, fn: (ev: unknown) => void) {
    (this.listeners[type] ??= []).push(fn);
  }

  send = vi.fn();

  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
    this.fire("close", {});
  });

  fire(type: string, ev: unknown) {
    (this.listeners[type] ?? []).forEach((fn) => fn(ev));
  }

  deliver(data: unknown) {
    this.fire("message", {
      data: typeof data === "string" ? data : JSON.stringify(data),
    });
  }
}

function envelope(eventName: string, payload: Record<string, unknown>) {
  return {
    type: "event",
    event_name: eventName,
    source_layer: "speech",
    timestamp_ns: Date.now() * 1_000_000,
    trace_id: "test-trace-id",
    payload,
  };
}

function Harness() {
  useWebSocket();
  return null;
}

describe("useWebSocket VoiceBootStatus handling", () => {
  const OriginalWS = globalThis.WebSocket;

  beforeEach(() => {
    (globalThis as unknown as { WebSocket: typeof MockWebSocket }).WebSocket =
      MockWebSocket;
    (window as unknown as { location: unknown }).location = {
      protocol: "http:",
      host: "localhost:5173",
    };
    useEventStore.setState({ voiceReady: false });
  });

  afterEach(() => {
    cleanup();
    (globalThis as unknown as { WebSocket: typeof WebSocket }).WebSocket =
      OriginalWS;
    MockWebSocket.last = null;
  });

  it("flips voiceReady true on a VoiceBootStatus { ready: true } frame", async () => {
    render(<Harness />);
    await Promise.resolve(); // let the queued "open" microtask run

    MockWebSocket.last!.deliver(envelope("VoiceBootStatus", { ready: true, detail: "warm" }));

    expect(useEventStore.getState().voiceReady).toBe(true);
  });

  it("flips voiceReady back to false on a VoiceBootStatus { ready: false } frame", async () => {
    useEventStore.setState({ voiceReady: true });
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(envelope("VoiceBootStatus", { ready: false, detail: "restart" }));

    expect(useEventStore.getState().voiceReady).toBe(false);
  });
});
