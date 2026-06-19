/**
 * Tests for the WikiSearch Ctrl-K command palette.
 *
 * Behaviour anchors:
 *   1. Ctrl-K opens the dialog.
 *   2. Typing fires a debounced fetch.
 *   3. Empty query shows the "Recent" section seeded from the tree cache.
 *   4. Clicking a result calls `onResultClick(slug)` and closes the dialog.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PropsWithChildren } from "react";

// jsdom does not ship `ResizeObserver`, but Radix Dialog (cmdk's underlying
// Dialog) calls it on mount. Provide a no-op polyfill before importing the
// component so the dialog can render under test.
class ResizeObserverPolyfill {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (typeof (globalThis as { ResizeObserver?: unknown }).ResizeObserver === "undefined") {
  (globalThis as unknown as { ResizeObserver: typeof ResizeObserverPolyfill }).ResizeObserver =
    ResizeObserverPolyfill;
}
// Radix Dialog also uses `hasPointerCapture` / `scrollIntoView` which jsdom
// lacks on Element.prototype.
if (typeof Element !== "undefined") {
  if (!("hasPointerCapture" in Element.prototype)) {
    // @ts-expect-error -- jsdom shim
    Element.prototype.hasPointerCapture = () => false;
  }
  if (!("scrollIntoView" in Element.prototype)) {
    // @ts-expect-error -- jsdom shim
    Element.prototype.scrollIntoView = () => {};
  }
}

import { WikiSearch } from "@/components/wiki/WikiSearch";

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
}

function seedTreeCache(client: QueryClient) {
  client.setQueryData(["wiki", "tree"], {
    ok: true,
    vault_root: "wiki/obsidian-vault",
    folders: [
      {
        name: "entities",
        kind: "entity",
        count: 2,
        files: [
          { slug: "ruben", title: "Ruben", mtime: 2000, size: 412 },
          { slug: "harald", title: "Harald", mtime: 1000, size: 289 },
        ],
      },
    ],
  });
}

function Wrapper({ children, client }: PropsWithChildren<{ client: QueryClient }>) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

describe("WikiSearch", () => {
  it("opens the palette when Ctrl-K is pressed", async () => {
    const client = makeClient();
    seedTreeCache(client);
    render(
      <Wrapper client={client}>
        <WikiSearch onResultClick={() => {}} />
      </Wrapper>,
    );
    expect(screen.queryByTestId("wiki-search-input")).toBeNull();
    await act(async () => {
      fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    });
    await waitFor(() => {
      expect(screen.getByTestId("wiki-search-input")).toBeDefined();
    });
  });

  it("typing fires a debounced fetch (200ms)", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({ ok: true, query: "pizza", hits: [] }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = makeClient();
    seedTreeCache(client);
    render(
      <Wrapper client={client}>
        <WikiSearch onResultClick={() => {}} />
      </Wrapper>,
    );

    // Open the palette.
    await act(async () => {
      fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    });
    const input = await screen.findByTestId("wiki-search-input");

    // Type rapidly: 'p', 'pi', 'pizza'.
    await act(async () => {
      fireEvent.change(input, { target: { value: "p" } });
      fireEvent.change(input, { target: { value: "pi" } });
      fireEvent.change(input, { target: { value: "pizza" } });
    });

    // Before debounce window expires, no fetch yet.
    expect(fetchMock).not.toHaveBeenCalled();

    // Advance past the 200 ms debounce.
    await act(async () => {
      vi.advanceTimersByTime(250);
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    const firstCall = fetchMock.mock.calls[0] as unknown as [string | URL];
    const calledUrl = String(firstCall[0]);
    expect(calledUrl).toContain("/api/wiki/search");
    expect(calledUrl).toContain("q=pizza");
  });

  it("shows the Recent section seeded from the tree cache when query is empty", async () => {
    const client = makeClient();
    seedTreeCache(client);
    render(
      <Wrapper client={client}>
        <WikiSearch onResultClick={() => {}} />
      </Wrapper>,
    );
    await act(async () => {
      fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    });
    await waitFor(() => {
      expect(screen.getByTestId("wiki-search-recent")).toBeDefined();
    });
    const items = screen.getAllByTestId("wiki-search-recent-item");
    expect(items).toHaveLength(2);
    // Sorted by mtime desc — ruben first (mtime=2000), harald second.
    expect(items[0].getAttribute("data-slug")).toBe("ruben");
    expect(items[1].getAttribute("data-slug")).toBe("harald");
  });

  it("clicking a result calls onResultClick(slug)", async () => {
    const client = makeClient();
    seedTreeCache(client);
    const onResultClick = vi.fn();
    render(
      <Wrapper client={client}>
        <WikiSearch onResultClick={onResultClick} />
      </Wrapper>,
    );
    await act(async () => {
      fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    });
    await waitFor(() => {
      expect(screen.getByTestId("wiki-search-recent")).toBeDefined();
    });
    const items = screen.getAllByTestId("wiki-search-recent-item");
    await act(async () => {
      fireEvent.click(items[0]);
    });
    expect(onResultClick).toHaveBeenCalledWith("ruben");
  });
});
