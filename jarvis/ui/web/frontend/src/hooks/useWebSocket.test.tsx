/**
 * The voice feature boots ~20s after the desktop window connects. The backend
 * announces readiness over the existing WS envelope as a `VoiceBootStatus`
 * event with `payload.ready: boolean`. This test drives the real
 * useWebSocket → WSClient → store path (via a MockWebSocket) and asserts that a
 * VoiceBootStatus frame flips `voiceReady` in the store.
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useWebSocket } from "@/hooks/useWebSocket";
import { useI18nStore } from "@/i18n";
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

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

function WebSocketHarness() {
  useWebSocket();
  return null;
}

function Harness() {
  return (
    <QueryClientProvider client={queryClient}>
      <WebSocketHarness />
    </QueryClientProvider>
  );
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
    useEventStore.setState({ voiceReady: false, toasts: [] });
    useI18nStore.getState().setUi("en", { push: false });
    queryClient.clear();
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

  it("surfaces a mission tool approval request globally without arguments", async () => {
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(
      envelope("ActionApprovalRequired", {
        mission_id: "mission-42",
        tool_name: "gmail/send_message",
        args_preview: "private content must not enter the toast",
      }),
    );

    const [toast] = useEventStore.getState().toasts;
    expect(toast.kind).toBe("warning");
    expect(toast.message).toContain("gmail/send_message");
    expect(toast.message).toContain("mission-42");
    expect(toast.message).not.toContain("private content");
  });

  it("invalidates every documentation query after a registry reload", async () => {
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    render(<Harness />);
    await Promise.resolve();

    MockWebSocket.last!.deliver(
      envelope("DocIndexReloaded", { total: 42, by_diataxis: {}, errors: 0 }),
    );

    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["docs"] });
  });
});

describe("useWebSocket connection state (welcome-gated + warming)", () => {
  const OriginalWS = globalThis.WebSocket;

  beforeEach(() => {
    (globalThis as unknown as { WebSocket: typeof MockWebSocket }).WebSocket =
      MockWebSocket;
    (window as unknown as { location: unknown }).location = {
      protocol: "http:",
      host: "localhost:5173",
    };
    (window as unknown as { __JARVIS_TOKEN?: string }).__JARVIS_TOKEN = undefined;
    useEventStore.setState({ connected: false, wsWarming: true });
  });

  afterEach(() => {
    cleanup();
    (globalThis as unknown as { WebSocket: typeof WebSocket }).WebSocket =
      OriginalWS;
    MockWebSocket.last = null;
  });

  it("marks connected only when the welcome frame arrives", async () => {
    render(<Harness />);
    await Promise.resolve(); // run the queued "open"
    // Raw socket open alone must NOT mark connected (the bootstrap also opens).
    expect(useEventStore.getState().connected).toBe(false);
    MockWebSocket.last!.deliver({
      type: "welcome",
      session_id: "s",
      version: "0.1.0",
      token: "t",
    });
    expect(useEventStore.getState().connected).toBe(true);
    expect(useEventStore.getState().wsWarming).toBe(false);
  });

  it("sets wsWarming on a 1013 close and clears it on a non-1013 close", async () => {
    render(<Harness />);
    await Promise.resolve();
    MockWebSocket.last!.fire("close", { code: 1013 });
    expect(useEventStore.getState().wsWarming).toBe(true);
    expect(useEventStore.getState().connected).toBe(false);
    MockWebSocket.last!.fire("close", { code: 1006 });
    expect(useEventStore.getState().wsWarming).toBe(false);
  });

  // 4401 = credential rejected at the handshake. On WebKit engines this is
  // the normal cookie-less handshake (BUG-065): while the client re-proves
  // its session via the one-time ticket, the UI must keep the "starting"
  // state instead of flashing OFFLINE; a failed mint (dead session) must
  // still surface the honest offline state.
  it("keeps warming through a 4401 ticket retry, drops it when the mint fails", async () => {
    const originalFetch = globalThis.fetch;
    try {
      (globalThis as unknown as { fetch: unknown }).fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ ticket: "one-time-abc", expires_in: 60 }),
      });
      render(<Harness />);
      await Promise.resolve();

      useEventStore.setState({ wsWarming: false });
      MockWebSocket.last!.fire("close", { code: 4401 });
      await waitFor(() => expect(useEventStore.getState().wsWarming).toBe(true));
      expect(useEventStore.getState().connected).toBe(false);

      (globalThis as unknown as { fetch: unknown }).fetch = vi
        .fn()
        .mockResolvedValue({ ok: false });
      MockWebSocket.last!.fire("close", { code: 4401 });
      await waitFor(() => expect(useEventStore.getState().wsWarming).toBe(false));
    } finally {
      (globalThis as unknown as { fetch: unknown }).fetch = originalFetch;
    }
  });
});
