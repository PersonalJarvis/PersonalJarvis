/**
 * ExplorePanel tests — the readable view over the knowledge base.
 *
 * The load-bearing case is emptiness, not the happy path: a knowledge base
 * that shows nothing has four different causes, the user cannot tell them
 * apart, and one of them once went undiagnosed for days behind a blank
 * screen. Each cause must produce its OWN message and its own way out.
 *
 * The graph is canvas-based, so it is stubbed here; its own encoding is
 * covered by lib/entityGraph.test.ts.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ExplorePanel } from "@/components/ultrawiki/ExplorePanel";

vi.mock("@/components/ultrawiki/EntityGraph", () => ({
  EntityGraph: () => <div data-testid="entity-graph-stub" />,
}));

const ENTITIES = [
  {
    key: "bora bora",
    label: "Bora Bora",
    mentions: 20,
    first_seen: "2026-06-30T10:00:00Z",
    last_seen: "2026-07-08T10:00:00Z",
    neighbors: [{ key: "tahiti", label: "Tahiti", shared: 10 }],
  },
  {
    key: "tahiti",
    label: "Tahiti",
    mentions: 18,
    first_seen: "2026-07-01T10:00:00Z",
    last_seen: "2026-07-13T10:00:00Z",
    neighbors: [{ key: "bora bora", label: "Bora Bora", shared: 10 }],
  },
  {
    key: "berlin",
    label: "Berlin",
    mentions: 3,
    first_seen: "2026-06-24T10:00:00Z",
    last_seen: "2026-07-25T10:00:00Z",
    neighbors: [],
  },
];

const MOMENTS = [
  {
    document_id: 1,
    item_id: 11,
    title: "How do I get to Bora Bora?",
    summary: "Routes via Tahiti.",
    resolution: "Fly via Tahiti.",
    entity_keys: ["bora bora", "tahiti"],
    timestamp_utc: "2026-07-08T10:00:00Z",
    month: "2026-07",
    source_id: "src1",
    source_label: "Jarvis Conversations",
    permalink: "app://a",
  },
];

const FULL_CORPUS = { sources: 1, items: 40, distilled: 30 };

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
}

function renderPanel() {
  return render(
    <QueryClientProvider client={makeClient()}>
      <ExplorePanel onOpenSources={() => {}} onOpenSettings={() => {}} />
    </QueryClientProvider>,
  );
}

function installRoutes(overrides: Record<string, unknown> = {}) {
  const responses: Record<string, unknown> = {
    "/api/ultrawiki/explore/entities": {
      ok: true,
      entities: ENTITIES,
      total: ENTITIES.length,
      corpus: FULL_CORPUS,
      reason: "ok",
    },
    "/api/ultrawiki/explore/moments": {
      ok: true,
      moments: MOMENTS,
      total: MOMENTS.length,
      corpus: FULL_CORPUS,
      reason: "ok",
    },
    ...overrides,
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const key = Object.keys(responses)
      .sort((a, b) => b.length - a.length)
      .find((prefix) => url.startsWith(prefix));
    if (!key) throw new Error(`unrouted fetch: ${url}`);
    const body = responses[key];
    if (body instanceof Error) throw body;
    return {
      ok: true,
      status: 200,
      json: async () => body,
    } as unknown as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("browsing", () => {
  it("lists topics with the most-mentioned first", async () => {
    installRoutes();
    renderPanel();

    const rows = await screen.findAllByTestId(/^explore-entity-/);
    expect(rows.map((row) => row.getAttribute("data-entity-key"))).toEqual([
      "bora bora",
      "tahiti",
      "berlin",
    ]);
  });

  it("shows how often a topic came up", async () => {
    installRoutes();
    renderPanel();

    const row = await screen.findByTestId("explore-entity-bora bora");
    expect(row.textContent).toContain("Bora Bora");
    expect(row.textContent).toContain("20");
  });

  it("gives every topic a time bar positioned inside the corpus span", async () => {
    installRoutes();
    renderPanel();

    const bar = await screen.findByTestId("explore-span-berlin");
    // Berlin runs the whole corpus; Bora Bora is a short late burst.
    const berlinWidth = Number(bar.getAttribute("data-width"));
    const boraBar = screen.getByTestId("explore-span-bora bora");
    expect(berlinWidth).toBeGreaterThan(Number(boraBar.getAttribute("data-width")));
  });

  it("filters the list as the user types, without refetching", async () => {
    const fetchMock = installRoutes();
    renderPanel();
    await screen.findByTestId("explore-entity-berlin");
    const callsBefore = fetchMock.mock.calls.length;

    fireEvent.change(screen.getByTestId("explore-search"), {
      target: { value: "bor" },
    });

    await waitFor(() => {
      expect(screen.queryByTestId("explore-entity-berlin")).toBeNull();
    });
    expect(screen.getByTestId("explore-entity-bora bora")).toBeTruthy();
    expect(fetchMock.mock.calls.length).toBe(callsBefore);
  });

  it("says so when a search matches nothing", async () => {
    installRoutes();
    renderPanel();
    await screen.findByTestId("explore-entity-berlin");

    fireEvent.change(screen.getByTestId("explore-search"), {
      target: { value: "zzz" },
    });

    expect(await screen.findByTestId("explore-no-search-hits")).toBeTruthy();
  });
});

describe("topic detail", () => {
  it("opens the moments of a topic when it is clicked", async () => {
    installRoutes({
      "/api/ultrawiki/explore/entities/tahiti": {
        ok: true,
        entity: ENTITIES[1],
        moments: MOMENTS,
        total: 1,
        corpus: FULL_CORPUS,
      },
    });
    renderPanel();

    fireEvent.click(await screen.findByTestId("explore-entity-tahiti"));

    expect(await screen.findByTestId("explore-detail")).toBeTruthy();
    expect(screen.getByTestId("explore-detail").textContent).toContain(
      "How do I get to Bora Bora?",
    );
  });

  it("offers the neighbours as a way to keep exploring", async () => {
    installRoutes({
      "/api/ultrawiki/explore/entities/tahiti": {
        ok: true,
        entity: ENTITIES[1],
        moments: MOMENTS,
        total: 1,
        corpus: FULL_CORPUS,
      },
    });
    renderPanel();

    fireEvent.click(await screen.findByTestId("explore-entity-tahiti"));

    const neighbour = await screen.findByTestId("explore-neighbor-bora bora");
    expect(neighbour.textContent).toContain("Bora Bora");
  });

  it("links every moment back to its evidence", async () => {
    installRoutes();
    renderPanel();

    const link = await screen.findByTestId("explore-moment-link-1");
    expect(link.getAttribute("href")).toBe("app://a");
  });

  it("returns to all moments from a topic", async () => {
    installRoutes({
      "/api/ultrawiki/explore/entities/tahiti": {
        ok: true,
        entity: ENTITIES[1],
        moments: MOMENTS,
        total: 1,
        corpus: FULL_CORPUS,
      },
    });
    renderPanel();

    fireEvent.click(await screen.findByTestId("explore-entity-tahiti"));
    await screen.findByTestId("explore-detail");

    fireEvent.click(screen.getByTestId("explore-reader-back"));

    await waitFor(() => {
      expect(screen.queryByTestId("explore-detail")).toBeNull();
    });
    expect(screen.getByTestId("explore-moment-1")).toBeTruthy();
  });
});

describe("reading a moment", () => {
  it("holds the answer back until the reader asks for it", async () => {
    // A moment carries a summary AND the resolution the distillation arrived
    // at. Printing both for two hundred moments is the wall of text this view
    // was rebuilt to stop being; the resolution was simply never rendered at
    // all, which is the other extreme. Closed = headline plus a few lines,
    // open = the whole thing.
    installRoutes();
    renderPanel();

    const card = await screen.findByTestId("explore-moment-1");
    expect(card.getAttribute("data-open")).toBe("false");
    expect(card.textContent).not.toContain("Fly via Tahiti.");

    fireEvent.click(within(card).getByRole("button"));

    expect(card.getAttribute("data-open")).toBe("true");
    expect(card.textContent).toContain("Fly via Tahiti.");
  });
});

describe("the three columns", () => {
  it("cannot be widened past the window by anything inside them", async () => {
    // The map paints a canvas at a fixed pixel width. A flex child that
    // cannot shrink below its content turns that into the section's floor —
    // which is how Explore grew a horizontal scrollbar that clipped the right
    // edge off every moment and could never shrink back out of it.
    installRoutes();
    renderPanel();
    await screen.findByTestId("explore-entity-berlin");

    const columns = screen.getByTestId("explore-columns");
    expect(columns.className).toContain("min-w-0");
    for (const column of Array.from(columns.children)) {
      expect(column.className).toContain("min-w-0");
    }
  });
});

describe("the four honest empty states", () => {
  const empty = (reason: string, corpus: Record<string, number>) => ({
    "/api/ultrawiki/explore/entities": {
      ok: true,
      entities: [],
      total: 0,
      corpus,
      reason,
    },
    "/api/ultrawiki/explore/moments": {
      ok: true,
      moments: [],
      total: 0,
      corpus,
      reason,
    },
  });

  it("names a missing source and offers the way there", async () => {
    installRoutes(empty("no_sources", { sources: 0, items: 0, distilled: 0 }));
    const onOpenSources = vi.fn();
    render(
      <QueryClientProvider client={makeClient()}>
        <ExplorePanel onOpenSources={onOpenSources} onOpenSettings={() => {}} />
      </QueryClientProvider>,
    );

    const banner = await screen.findByTestId("explore-empty");
    expect(banner.getAttribute("data-reason")).toBe("no_sources");
    fireEvent.click(screen.getByTestId("explore-empty-action"));
    expect(onOpenSources).toHaveBeenCalled();
  });

  it("distinguishes a source that never imported", async () => {
    installRoutes(
      empty("nothing_imported", { sources: 1, items: 0, distilled: 0 }),
    );
    renderPanel();

    const banner = await screen.findByTestId("explore-empty");
    expect(banner.getAttribute("data-reason")).toBe("nothing_imported");
  });

  it("sends an undistilled corpus to the settings, not to the sources", async () => {
    installRoutes(
      empty("nothing_distilled", { sources: 1, items: 40, distilled: 0 }),
    );
    const onOpenSettings = vi.fn();
    render(
      <QueryClientProvider client={makeClient()}>
        <ExplorePanel onOpenSources={() => {}} onOpenSettings={onOpenSettings} />
      </QueryClientProvider>,
    );

    const banner = await screen.findByTestId("explore-empty");
    expect(banner.getAttribute("data-reason")).toBe("nothing_distilled");
    fireEvent.click(screen.getByTestId("explore-empty-action"));
    expect(onOpenSettings).toHaveBeenCalled();
  });

  it("keeps the moments readable when only the topic layer is empty", async () => {
    installRoutes({
      "/api/ultrawiki/explore/entities": {
        ok: true,
        entities: [],
        total: 0,
        corpus: FULL_CORPUS,
        reason: "no_entities",
      },
    });
    renderPanel();

    expect((await screen.findByTestId("explore-empty")).getAttribute("data-reason")).toBe(
      "no_entities",
    );
    // The moments still arrived — an empty topic list must not hide them.
    expect(await screen.findByTestId("explore-moment-1")).toBeTruthy();
  });
});

describe("failure", () => {
  it("reports a dead knowledge base instead of rendering an empty shell", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );
    renderPanel();

    expect(await screen.findByTestId("explore-error")).toBeTruthy();
  });
});
