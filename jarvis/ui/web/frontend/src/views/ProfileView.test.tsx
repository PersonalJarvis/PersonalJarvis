/**
 * Component tests for the rebuilt Profile section.
 *
 * What these pin is the contract the redesign was built on: the page states
 * facts and folds gaps (rather than printing "not known yet" eighteen times),
 * every known fact can show the sentence it was learned from, and each rail
 * card has a designed empty state instead of a dead box.
 *
 * The three cards that were deleted are guarded too — the review queue, the
 * people list and the "would love to know" prompt were all structurally unable
 * to fill, and a later change should not quietly bring them back.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ProfileView } from "@/views/ProfileView";

// SourceCard subscribes to a WS client in a useEffect; null keeps the effect a
// deterministic no-op in jsdom.
vi.mock("@/hooks/useWebSocket", () => ({
  getWSClient: () => null,
}));

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

interface RouteResult {
  status?: number;
  body: unknown;
}

/**
 * Mock `fetch` with per-route status control. Unknown URLs throw so accidental
 * network calls surface as test failures.
 */
function installFetchMock(routes: Record<string, () => RouteResult>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    for (const prefix of Object.keys(routes)) {
      if (url.startsWith(prefix)) {
        const { status = 200, body } = routes[prefix]();
        return {
          ok: status >= 200 && status < 300,
          status,
          statusText: status === 503 ? "Service Unavailable" : "OK",
          json: async () => body,
          text: async () => JSON.stringify(body),
        } as Response;
      }
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

const RAW = `---
identity:
  name: Ruben
---

## Observations over time

<!-- curator:observations:start -->
- [2026-08-29] identity.primary_language: de — "always answer me in German"
<!-- curator:observations:end -->

## Do Not Record

- Political or religious beliefs (echo-chamber risk)
- MBTI type or similar pseudo-scientific labels
`;

const PROFILE_OK = {
  user: {
    name: "Ruben",
    meta: {
      identity: { name: "Ruben", primary_language: "de", timezone: "Europe/Berlin" },
      last_updated: "2026-08-29",
    },
    path: "data/workspace/USER.md",
  },
  people: [],
  reviews_count: 0,
  has_avatar: false,
};

const BASE_ROUTES: Record<string, () => RouteResult> = {
  "/api/profile/raw": () => ({
    body: { content: RAW, path: "USER.md", mtime_ms: 1, size_bytes: RAW.length },
  }),
  "/api/profile": () => ({ body: PROFILE_OK }),
  "/api/board/personal/summary": () => ({
    body: {
      window_days: 30,
      totals: { session_count: 491, first_day: "2026-08-03" },
      window: {},
      streak_days: 10,
      longest_streak: 13,
    },
  }),
  "/api/board/bio": () => ({ body: { text: null, generated_at: null } }),
  "/api/settings/agent-instructions": () => ({
    body: { content: "", exists: false, filename: "George.md", template: "", char_count: 0 },
  }),
  "/api/wiki/page/": () => ({ status: 404, body: { detail: "no such page" } }),
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("the file states facts and folds gaps", () => {
  it("shows the known facts and never repeats 'not known yet' for the rest", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    await screen.findByText("Europe/Berlin");

    // Sixteen empty fields exist, and none of them is on the page yet.
    expect(screen.queryByText("not known yet")).toBeNull();
  });

  it("counts the entries in the header instead of scoring the reader", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    const count = await screen.findByTestId("dossier-count");
    expect(count.textContent).toBe("3 of 18 entries");
    // The old header asked "Who are you?" over a progress bar.
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("puts the reader's name at the top, not a stage name", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    const header = await screen.findByTestId("dossier-header");
    expect(header.textContent).toContain("Ruben");
    // The board summary lands a tick later; the line grows rather than
    // reserving a slot with a zero in it.
    await waitFor(() => expect(header.textContent).toContain("491 conversations"));
    expect(header.textContent).toContain("since");
  });

  it("opens a cluster's gaps on demand", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    const toggle = await screen.findByTestId("gaps-communication");
    expect(toggle.textContent).toContain("5 open fields");
    expect(screen.queryByText("Verbosity")).toBeNull();

    fireEvent.click(toggle);
    await screen.findByText("Verbosity");
  });
});

describe("provenance", () => {
  it("shows the date a fact was learned and the sentence behind it", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    const source = await screen.findByTestId("entry-source-primary_language");
    expect(source.textContent).toContain("2026-08-29");

    fireEvent.click(source);
    await screen.findByText("always answer me in German");
  });

  it("shows no date for a fact the audit trail never mentions", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    await screen.findByText("Europe/Berlin");
    expect(screen.queryByTestId("entry-source-timezone")).toBeNull();
  });
});

describe("the rail", () => {
  it("offers to write the portrait when none exists", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    const empty = await screen.findByTestId("portrait-empty");
    expect(empty.textContent).toContain("has not written a portrait");
  });

  it("renders the portrait and its feedback when one exists", async () => {
    installFetchMock({
      ...BASE_ROUTES,
      "/api/board/bio": () => ({
        body: { text: "Builds things at night.", generated_at: "2026-09-01T20:00:00Z" },
      }),
    });
    renderWithClient(<ProfileView />);

    const text = await screen.findByTestId("portrait-text");
    expect(text.textContent).toBe("Builds things at night.");
    expect(screen.getByRole("button", { name: "Fits" })).toBeTruthy();
  });

  it("quotes the never-recorded categories out of the file", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    const list = await screen.findByTestId("never-stored");
    expect(list.textContent).toContain("Political");
    expect(list.textContent).toContain("MBTI type");
  });

  it("invites the wiki page rather than reporting a 404", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    const empty = await screen.findByTestId("wiki-empty");
    expect(empty.textContent).toContain("no page about you yet");
  });
});

describe("the dead cards stay deleted", () => {
  it("renders no review queue, no people list and no ask prompt", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    await screen.findByTestId("dossier-header");
    await waitFor(() => expect(screen.queryByTestId("never-stored")).not.toBeNull());

    expect(screen.queryByTestId("ask-next")).toBeNull();
    expect(screen.queryByTestId("reviews-disabled")).toBeNull();
    expect(screen.queryByTestId("reviews-error")).toBeNull();
    expect(screen.queryByText("Waiting for your OK")).toBeNull();
    expect(screen.queryByText("No people known yet")).toBeNull();
  });

  it("never calls the review endpoint", async () => {
    const fetchMock = installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    await screen.findByTestId("dossier-header");
    const called = fetchMock.mock.calls.map((c) => String(c[0]));
    expect(called.some((u) => u.includes("/api/profile/reviews"))).toBe(false);
  });
});

describe("a profile the backend is not serving", () => {
  it("treats 503 as a state, not a failure", async () => {
    installFetchMock({
      ...BASE_ROUTES,
      "/api/profile": () => ({ status: 503, body: { detail: "Profile system not ready." } }),
    });
    renderWithClient(<ProfileView />);

    await screen.findByText("No name on file");
    expect(screen.queryByTestId("dossier-header")).toBeNull();
  });
});
