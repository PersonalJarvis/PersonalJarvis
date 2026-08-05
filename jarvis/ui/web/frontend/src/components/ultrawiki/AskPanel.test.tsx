/**
 * AskPanel tests — hybrid-search results with citations.
 *
 * Pins that a submitted query hits `POST /api/ultrawiki/ask`, renders the
 * synthesized cited answer, and that a result row renders title, snippet, source, matched-by chips
 * (keyword / semantic), timestamp, and the permalink as a clickable citation.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { AskPanel } from "@/components/ultrawiki/AskPanel";
import type { UltraWikiSearchHit } from "@/lib/ultrawikiApi";

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

const HIT: UltraWikiSearchHit = {
  item_id: 42,
  source_id: "local-folder-abc123",
  title: "Trip planning notes",
  snippet: "The ferry to the island leaves at 09:30 on weekdays.",
  permalink: "obsidian://open?path=notes%2Ftrip.md",
  timestamp_utc: "2026-07-01T08:00:00Z",
  score: 0.87,
  matched_by: ["keyword", "vector"],
  // The rerank stage is optional: an ungraded hit is the DEFAULT shape, so
  // the row must render without a grade chip or a context disclosure.
  rerank_score: null,
  context: [],
  // Empty means "the item's own stamp IS timestamp_utc" — the shape every
  // leg but the event leg produces.
  recorded_utc: "",
  // Passage provenance: the vector leg names the chunk it matched, the
  // keyword leg cannot, and null is that honest "cannot say".
  document_id: null,
  chunk_index: null,
  char_start: null,
  char_end: null,
};

function installFetchMock(routes: Record<string, () => unknown>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    for (const prefix of Object.keys(routes)) {
      if (url.startsWith(prefix)) {
        return {
          ok: true,
          status: 200,
          statusText: "OK",
          json: async () => routes[prefix](),
        } as Response;
      }
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AskPanel — results with citations", () => {
  it("submits a query and renders the hit with matched_by chips and permalink", async () => {
    const fetchMock = installFetchMock({
      "/api/ultrawiki/ask": () => ({
        query: "ferry",
        question: "ferry",
        answer: "The ferry leaves at 09:30 on weekdays [1].",
        answer_status: "answered",
        provider: "fake",
        citations: [1],
        results: [HIT],
        total: 1,
      }),
    });
    renderWithClient(
      <AskPanel
        searchLegs={{
          keyword: { available: true },
          vector: { available: true, backend: "ollama", model: "bge-m3" },
        }}
      />,
    );

    fireEvent.change(screen.getByTestId("ultrawiki-ask-input"), {
      target: { value: "ferry" },
    });
    fireEvent.click(screen.getByTestId("ultrawiki-ask-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-hit-42")).toBeDefined();
    });
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/ultrawiki/ask");
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: "POST" });
    expect(screen.getByTestId("ultrawiki-answer").textContent).toContain(
      "ferry leaves at 09:30",
    );

    const row = screen.getByTestId("ultrawiki-hit-42");
    expect(row.textContent).toContain("Trip planning notes");
    expect(row.textContent).toContain("ferry to the island");
    expect(row.textContent).toContain("local-folder-abc123");
    expect(row.textContent).toContain("2026-07-01T08:00:00Z");

    // matched_by chips: "keyword" and "vector" (rendered as "semantic").
    expect(
      screen.getByTestId("ultrawiki-matched-42-keyword").textContent,
    ).toBe("keyword");
    expect(
      screen.getByTestId("ultrawiki-matched-42-vector").textContent,
    ).toBe("semantic");

    // The citation permalink is a real anchor pointing at the original.
    const link = screen.getByTestId(
      "ultrawiki-permalink-42",
    ) as HTMLAnchorElement;
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toBe(
      "obsidian://open?path=notes%2Ftrip.md",
    );
  });

  it("keeps evidence visible when synthesis is unavailable", async () => {
    installFetchMock({
      "/api/ultrawiki/ask": () => ({
        query: "ferry",
        question: "ferry",
        answer: "",
        answer_status: "answer_unavailable",
        provider: "",
        citations: [],
        synthesis_error: "no credential-ready chat provider is available",
        results: [HIT],
        total: 1,
      }),
    });
    renderWithClient(
      <AskPanel searchLegs={{ keyword: { available: true } }} ingestedItems={1} />,
    );

    fireEvent.change(screen.getByTestId("ultrawiki-ask-input"), {
      target: { value: "ferry" },
    });
    fireEvent.click(screen.getByTestId("ultrawiki-ask-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-answer-unavailable")).toBeDefined();
      expect(screen.getByTestId("ultrawiki-hit-42")).toBeDefined();
    });
  });

  it("shows an honest insufficiency answer without pretending evidence supports it", async () => {
    installFetchMock({
      "/api/ultrawiki/ask": () => ({
        query: "ferry",
        question: "ferry",
        answer: "The retrieved notes do not contain the ferry schedule.",
        answer_status: "insufficient_evidence",
        provider: "fake",
        citations: [],
        results: [HIT],
        total: 1,
      }),
    });
    renderWithClient(
      <AskPanel searchLegs={{ keyword: { available: true } }} ingestedItems={1} />,
    );

    fireEvent.change(screen.getByTestId("ultrawiki-ask-input"), {
      target: { value: "ferry" },
    });
    fireEvent.click(screen.getByTestId("ultrawiki-ask-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-insufficient-evidence")).toBeDefined();
    });
    expect(screen.getByTestId("ultrawiki-insufficient-evidence").textContent).toContain(
      "do not contain the ferry schedule",
    );
    expect(screen.queryByTestId("ultrawiki-answer")).toBeNull();
  });

  it("names the credential carrying a live leg, never the key itself", async () => {
    installFetchMock({ "/api/ultrawiki/ask": () => ({ query: "", results: [], total: 0 }) });
    renderWithClient(
      <AskPanel
        searchLegs={{
          keyword: { available: true },
          vector: { available: true, backend: "gemini", model: "gemini-embedding-001" },
          rerank: { available: false, provider: "off" },
        }}
        slots={{
          embedding: {
            provider: "gemini",
            ready: true,
            reason: "",
            via: "your saved Gemini API key (gemini_api_key)",
          },
        }}
        ingestedItems={12}
      />,
    );

    const vector = screen.getByTestId("ultrawiki-leg-vector");
    expect(vector.textContent).toContain("Semantic search: on");
    expect(vector.textContent).toContain(
      "via your saved Gemini API key (gemini_api_key)",
    );
  });

  it("guides the user instead of showing a blank panel when nothing is ingested", async () => {
    installFetchMock({ "/api/ultrawiki/ask": () => ({ query: "", results: [], total: 0 }) });
    const onOpenSources = vi.fn();
    renderWithClient(
      <AskPanel
        searchLegs={{ keyword: { available: true } }}
        ingestedItems={0}
        onOpenSources={onOpenSources}
      />,
    );

    const guide = screen.getByTestId("ultrawiki-ask-nothing-ingested");
    expect(guide.textContent).toContain("Nothing has been ingested yet");
    expect(guide.textContent).toContain("approve a source");
    expect(guide.textContent).toContain("Sync");
    fireEvent.click(screen.getByTestId("ultrawiki-ask-open-sources"));
    expect(onOpenSources).toHaveBeenCalledTimes(1);
  });

  it("keeps the normal empty state once items exist but a query finds nothing", async () => {
    installFetchMock({
      "/api/ultrawiki/ask": () => ({ query: "zzz", results: [], total: 0 }),
    });
    renderWithClient(
      <AskPanel searchLegs={{ keyword: { available: true } }} ingestedItems={7} />,
    );
    expect(screen.queryByTestId("ultrawiki-ask-nothing-ingested")).toBeNull();

    fireEvent.change(screen.getByTestId("ultrawiki-ask-input"), {
      target: { value: "zzz" },
    });
    fireEvent.click(screen.getByTestId("ultrawiki-ask-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-ask-empty")).toBeDefined();
    });
  });

  it("shows the honest degraded-leg reason when a query finds nothing", async () => {
    installFetchMock({
      "/api/ultrawiki/ask": () => ({ query: "x", results: [], total: 0 }),
    });
    renderWithClient(
      <AskPanel
        searchLegs={{
          keyword: { available: true },
          vector: { available: false, reason: "no embedding provider is configured" },
        }}
      />,
    );

    fireEvent.change(screen.getByTestId("ultrawiki-ask-input"), {
      target: { value: "anything" },
    });
    fireEvent.click(screen.getByTestId("ultrawiki-ask-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-ask-empty")).toBeDefined();
    });
    expect(screen.getByTestId("ultrawiki-ask-empty").textContent).toContain(
      "no embedding provider is configured",
    );
  });
});
