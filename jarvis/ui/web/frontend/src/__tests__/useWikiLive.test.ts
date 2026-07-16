import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, ReactNode } from "react";
import { useWikiLive } from "@/hooks/useWikiLive";

/** Minimal mock for WebSocket with onopen/onmessage/onclose/onerror plumbing. */
class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  readyState = MockWebSocket.OPEN;
  static instances: MockWebSocket[] = [];

  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;

  url: string;
  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
    if (this.onclose) this.onclose({});
  });

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
    // Fire open on the next tick so the consumer's effect has run.
    queueMicrotask(() => {
      if (this.onopen) this.onopen({});
    });
  }

  deliver(payload: unknown) {
    const data = typeof payload === "string" ? payload : JSON.stringify(payload);
    if (this.onmessage) this.onmessage({ data });
  }
}

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return createElement(QueryClientProvider, { client: qc }, children);
}

describe("useWikiLive", () => {
  const OriginalWS = globalThis.WebSocket;

  beforeEach(() => {
    MockWebSocket.instances = [];
    (globalThis as any).WebSocket = MockWebSocket;
    Object.defineProperty(window, "location", {
      writable: true,
      value: { protocol: "http:", host: "localhost:5173" },
    });
  });

  afterEach(() => {
    (globalThis as any).WebSocket = OriginalWS;
  });

  it("opens a WebSocket on mount and sets connected=true after open", async () => {
    const { result } = renderHook(() => useWikiLive(), { wrapper });
    expect(MockWebSocket.instances.length).toBe(1);
    expect(MockWebSocket.instances[0].url).toContain("/api/wiki/live");

    // Wait a microtask so the queued open fires.
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.connected).toBe(true);
  });

  it("refreshes every wiki projection when the socket opens", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(qc, "invalidateQueries");
    const customWrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: qc }, children);

    renderHook(() => useWikiLive(), { wrapper: customWrapper });
    await act(async () => {
      await Promise.resolve();
    });

    const keys = spy.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toEqual([
      ["wiki", "tree"],
      ["wiki", "graph"],
      ["wiki", "health"],
      ["wiki", "search"],
      ["wiki", "page"],
      ["wiki", "backlinks"],
    ]);
  });

  it("refreshes every wiki projection on page_changed", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(qc, "invalidateQueries");

    const customWrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: qc }, children);

    const { result } = renderHook(() => useWikiLive(), { wrapper: customWrapper });
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.connected).toBe(true);
    spy.mockClear();

    await act(async () => {
      MockWebSocket.instances[0].deliver({
        type: "page_changed",
        slug: "sam",
        path: "entities/sam.md",
        kind: "modified",
      });
      await Promise.resolve();
    });

    const keys = spy.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toEqual([
      ["wiki", "tree"],
      ["wiki", "graph"],
      ["wiki", "health"],
      ["wiki", "search"],
      ["wiki", "page"],
      ["wiki", "backlinks"],
    ]);
    expect(result.current.lastEventAt).not.toBeNull();
  });

  it("closes the WebSocket on unmount and stops further work", async () => {
    const { unmount } = renderHook(() => useWikiLive(), { wrapper });
    await act(async () => {
      await Promise.resolve();
    });
    const ws = MockWebSocket.instances[0];
    expect(ws.close).not.toHaveBeenCalled();

    unmount();
    expect(ws.close).toHaveBeenCalled();
    // After unmount, no further reconnect happens even if we wait.
    const before = MockWebSocket.instances.length;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(MockWebSocket.instances.length).toBe(before);
  });

  it("ignores malformed messages", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(qc, "invalidateQueries");

    const customWrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: qc }, children);
    renderHook(() => useWikiLive(), { wrapper: customWrapper });
    await act(async () => {
      await Promise.resolve();
    });
    spy.mockClear();

    await act(async () => {
      MockWebSocket.instances[0].deliver("not json at all");
      MockWebSocket.instances[0].deliver({ type: "something_else" });
      await Promise.resolve();
    });
    // The spy must NOT have been called — malformed and unrelated
    // messages are silently dropped.
    expect(spy).not.toHaveBeenCalled();
  });
});
