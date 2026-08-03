/**
 * WordSearchPanel tests.
 *
 * The load-bearing cases are the ones a blank screen would hide: each empty
 * outcome has its OWN named cause and its own way out, neighbours derived
 * from co-occurrence must not be presented as neighbours by meaning, and a
 * hit has to say WHICH passage answered — the span is the whole point of the
 * feature, and rendering only a snippet would silently undo it.
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

import { WordSearchPanel } from "@/components/ultrawiki/WordSearchPanel";

const NEIGHBOURS = [
  { term: "sailing", similarity: 0.94, doc_freq: 12 },
  { term: "mainsail", similarity: 0.88, doc_freq: 7 },
];

const HIT = {
  item_id: 11,
  source_id: "src1",
  title: "Race notes",
  snippet: "the regatta briefing",
  permalink: "app://a",
  timestamp_utc: "2026-07-08T10:00:00Z",
  score: 0.42,
  matched_by: ["word", "semantic"],
  passages: [
    {
      document_id: 91,
      chunk_index: 4,
      char_start: 3600,
      char_end: 4480,
      text: "the regatta briefing and the mainsail trim we agreed on",
      terms: ["regatta", "mainsail"],
      score: 1.88,
    },
  ],
};

const OK_RESPONSE = {
  word: "regatta",
  status: "ok",
  neighbour_source: "vector",
  reason: "",
  neighbours: NEIGHBOURS,
  results: [HIT],
  total: 1,
  lexicon: { terms: 900, embedded_terms: 900, items: 40, passages: 300 },
};

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
}

function renderPanel(props: Record<string, unknown> = {}) {
  return render(
    <QueryClientProvider client={makeClient()}>
      <WordSearchPanel {...props} />
    </QueryClientProvider>,
  );
}

/** Route every word-search call to `body`, recording the URLs asked for. */
function installRoute(body: unknown) {
  const urls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    urls.push(url);
    if (!url.startsWith("/api/ultrawiki/word-search")) {
      throw new Error(`unrouted fetch: ${url}`);
    }
    if (body instanceof Error) throw body;
    return { ok: true, status: 200, json: async () => body } as unknown as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return urls;
}

async function searchFor(word: string) {
  fireEvent.change(screen.getByTestId("ultrawiki-word-input"), {
    target: { value: word },
  });
  fireEvent.click(screen.getByTestId("ultrawiki-word-submit"));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the neighbourhood", () => {
  it("shows the nearest words and sends the query to the backend", async () => {
    const urls = installRoute(OK_RESPONSE);
    renderPanel();
    await searchFor("regatta");

    const chips = await screen.findByTestId("ultrawiki-word-neighbours");
    expect(within(chips).getByText("sailing")).toBeTruthy();
    expect(within(chips).getByText("mainsail")).toBeTruthy();
    expect(urls.some((url) => url.includes("word=regatta"))).toBe(true);
  });

  it("re-runs the search when a neighbour chip is clicked", async () => {
    const urls = installRoute(OK_RESPONSE);
    renderPanel();
    await searchFor("regatta");
    fireEvent.click(await screen.findByTestId("ultrawiki-word-chip-sailing"));

    await waitFor(() =>
      expect(urls.some((url) => url.includes("word=sailing"))).toBe(true),
    );
  });

  it("does not present neighbours by company as neighbours by meaning", async () => {
    // The two similarity numbers are not comparable, so the heading has to
    // say which one the reader is looking at.
    installRoute({
      ...OK_RESPONSE,
      neighbour_source: "cooccurrence",
      reason: "nothing has been embedded yet",
    });
    renderPanel();
    await searchFor("regatta");

    const chips = await screen.findByTestId("ultrawiki-word-neighbours");
    expect(chips.textContent).toContain("keep company");
    expect(chips.textContent).not.toContain("Closest in meaning");
    expect(
      (await screen.findByTestId("ultrawiki-word-reason")).textContent,
    ).toContain("nothing has been embedded yet");
  });
});

describe("passages", () => {
  it("names the passage that answered, with its character span", async () => {
    installRoute(OK_RESPONSE);
    renderPanel();
    await searchFor("regatta");

    const passage = await screen.findByTestId("ultrawiki-word-passage-11-91");
    // chunk_index is zero-based on the wire and one-based on screen.
    expect(passage.textContent).toContain("passage 5");
    expect(passage.textContent).toContain("3600");
    expect(passage.textContent).toContain("4480");
    expect(passage.textContent).toContain("regatta, mainsail");
  });

  it("falls back to the snippet when the item has no stored passages", async () => {
    installRoute({ ...OK_RESPONSE, results: [{ ...HIT, passages: [] }] });
    renderPanel();
    await searchFor("regatta");

    expect(
      (await screen.findByTestId("ultrawiki-word-snippet-11")).textContent,
    ).toContain("the regatta briefing");
  });

  it("labels which leg found the hit", async () => {
    installRoute(OK_RESPONSE);
    renderPanel();
    await searchFor("regatta");

    expect(await screen.findByTestId("ultrawiki-word-leg-11-word")).toBeTruthy();
    expect(screen.getByTestId("ultrawiki-word-leg-11-semantic")).toBeTruthy();
  });
});

describe("empty outcomes name their own cause", () => {
  it("sends an empty store to the sources screen", async () => {
    installRoute({
      ...OK_RESPONSE,
      status: "empty_index",
      reason: "nothing has been imported yet",
      neighbours: [],
      results: [],
      total: 0,
      lexicon: {},
    });
    const onOpenSources = vi.fn();
    renderPanel({ onOpenSources });
    await searchFor("regatta");

    expect(
      (await screen.findByTestId("ultrawiki-word-reason")).textContent,
    ).toContain("nothing has been imported yet");
    fireEvent.click(screen.getByTestId("ultrawiki-word-open-sources"));
    expect(onOpenSources).toHaveBeenCalled();
  });

  it("tells an unknown word apart from a word that ranks nowhere", async () => {
    installRoute({
      ...OK_RESPONSE,
      status: "unknown_word",
      reason: "'zzz' does not occur anywhere in the imported text",
      neighbours: [],
      results: [],
      total: 0,
    });
    renderPanel();
    await searchFor("zzz");

    expect(
      (await screen.findByTestId("ultrawiki-word-reason")).textContent,
    ).toContain("does not occur anywhere");
    expect(screen.queryByTestId("ultrawiki-word-results")).toBeNull();
  });

  it("offers the settings screen when the neighbourhood is unavailable", async () => {
    installRoute({
      ...OK_RESPONSE,
      status: "neighbours_unavailable",
      neighbour_source: "none",
      reason: "no embedding provider is configured",
      neighbours: [],
    });
    const onOpenSettings = vi.fn();
    renderPanel({ onOpenSettings });
    await searchFor("regatta");

    fireEvent.click(await screen.findByTestId("ultrawiki-word-open-settings"));
    expect(onOpenSettings).toHaveBeenCalled();
    // The hits are still shown: the word search ran, only the expansion did not.
    expect(screen.getByTestId("ultrawiki-word-results")).toBeTruthy();
  });

  it("says the word index is still filling instead of showing nothing", async () => {
    installRoute({
      ...OK_RESPONSE,
      status: "no_matches",
      reason: "nothing survived ranking",
      results: [],
      total: 0,
      lexicon: { terms: 900, embedded_terms: 120 },
    });
    renderPanel();
    await searchFor("regatta");

    const note = await screen.findByTestId("ultrawiki-word-lexicon");
    expect(note.textContent).toContain("120");
    expect(note.textContent).toContain("900");
  });

  it("reports a failed request instead of an empty panel", async () => {
    installRoute(new Error("boom"));
    renderPanel();
    await searchFor("regatta");

    expect((await screen.findByTestId("ultrawiki-word-error")).textContent).toContain(
      "boom",
    );
  });
});
