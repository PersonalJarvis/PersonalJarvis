import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, ReactNode } from "react";
import { useWikiLive } from "@/hooks/useWikiLive";

/** Minimal event-listener WebSocket mock compatible with the shared client. */
class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readyState = MockWebSocket.OPEN;
  readonly url: string;
  private listeners: Record<string, Array<(event: any) => void>> = {};

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
    queueMicrotask(() => this.fire("open", {}));
  }

  addEventListener(type: string, listener: (event: any) => void) {
    (this.listeners[type] ??= []).push(listener);
  }

  send = vi.fn();

  close = vi.fn(() => {
    if (this.readyState === MockWebSocket.CLOSED) return;
    this.readyState = MockWebSocket.CLOSED;
    this.fire("close", { code: 1000 });
  });

  fire(type: string, event: any) {
    if (type === "close") this.readyState = MockWebSocket.CLOSED;
    for (const listener of this.listeners[type] ?? []) listener(event);
  }

  deliver(payload: unknown) {
    const data = typeof payload === "string" ? payload : JSON.stringify(payload);
    this.fire("message", { data });
  }
}

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return createElement(QueryClientProvider, { client: queryClient }, children);
}

describe("useWikiLive", () => {
  const originalWebSocket = globalThis.WebSocket;
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    MockWebSocket.instances = [];
    (globalThis as any).WebSocket = MockWebSocket;
    Object.defineProperty(window, "location", {
      writable: true,
      value: { protocol: "http:", host: "localhost:5173" },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    (globalThis as any).WebSocket = originalWebSocket;
    (globalThis as any).fetch = originalFetch;
  });

  it("opens the live socket and reports the connected state", async () => {
    const { result } = renderHook(() => useWikiLive(), { wrapper });

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].url).toBe(
      "ws://localhost:5173/api/wiki/live",
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.connected).toBe(true);
  });

  it("refreshes every wiki projection when the socket opens", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const customWrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    renderHook(() => useWikiLive(), { wrapper: customWrapper });
    await act(async () => {
      await Promise.resolve();
    });

    expect(invalidate.mock.calls.map((call) => call[0]?.queryKey)).toEqual([
      ["wiki", "tree"],
      ["wiki", "graph"],
      ["wiki", "health"],
      ["wiki", "search"],
      ["wiki", "page"],
      ["wiki", "backlinks"],
    ]);
  });

  it("refreshes every wiki projection on page_changed", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const customWrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const { result } = renderHook(() => useWikiLive(), {
      wrapper: customWrapper,
    });
    await act(async () => {
      await Promise.resolve();
    });
    invalidate.mockClear();

    await act(async () => {
      MockWebSocket.instances[0].deliver({
        type: "page_changed",
        slug: "nova",
        path: "entities/nova.md",
        kind: "modified",
      });
      await Promise.resolve();
    });

    expect(invalidate.mock.calls.map((call) => call[0]?.queryKey)).toEqual([
      ["wiki", "tree"],
      ["wiki", "graph"],
      ["wiki", "health"],
      ["wiki", "search"],
      ["wiki", "page"],
      ["wiki", "backlinks"],
    ]);
    expect(result.current.lastEventAt).not.toBeNull();
  });

  it("retries a 4401 WebKit rejection with a one-time ticket", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ticket: "wiki-ticket", expires_in: 60 }),
    });
    (globalThis as any).fetch = fetchMock;

    const { result, unmount } = renderHook(() => useWikiLive(), { wrapper });
    const firstSocket = MockWebSocket.instances[0];

    await act(async () => {
      firstSocket.fire("close", { code: 4401 });
      await vi.advanceTimersByTimeAsync(500);
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ui/ws-ticket",
      expect.objectContaining({
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
      }),
    );
    expect(MockWebSocket.instances).toHaveLength(2);
    expect(MockWebSocket.instances[1].url).toBe(
      "ws://localhost:5173/api/wiki/live?ticket=wiki-ticket",
    );
    expect(result.current.connected).toBe(true);

    unmount();
  });

  it("closes the socket on unmount without reconnecting", async () => {
    vi.useFakeTimers();
    const { unmount } = renderHook(() => useWikiLive(), { wrapper });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const socket = MockWebSocket.instances[0];

    unmount();
    expect(socket.close).toHaveBeenCalledOnce();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("ignores malformed and unrelated messages", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const customWrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    renderHook(() => useWikiLive(), { wrapper: customWrapper });
    await act(async () => {
      await Promise.resolve();
    });
    invalidate.mockClear();

    await act(async () => {
      MockWebSocket.instances[0].deliver("not json at all");
      MockWebSocket.instances[0].deliver({ type: "something_else" });
      await Promise.resolve();
    });

    expect(invalidate).not.toHaveBeenCalled();
  });
});
