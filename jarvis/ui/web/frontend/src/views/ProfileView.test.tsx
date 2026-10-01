/**
 * Component tests for the Profile section.
 *
 * What these pin: known details are rows and missing ones are one line of
 * "add" chips (never a wall of "not set"), every known detail can show the
 * sentence it was learned from, an unnamed profile asks for a name, a portrait
 * that stops mid-word is never shown as if it were finished, and the raw file
 * opens as a sub-page. The dead review queue stays deleted.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

describe("details", () => {
  it("shows known details as rows and missing ones as add chips", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    await screen.findByText("Europe/Berlin");
    const missing = await screen.findByTestId("missing-talk");
    expect(missing.textContent).toContain("Answer length");
    // No wall of empty rows.
    expect(screen.queryByTestId("field-verbosity")).toBeNull();
    expect(screen.queryByText("not known yet")).toBeNull();
  });

  it("opens the editor for a missing detail from its chip", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    const missing = await screen.findByTestId("missing-talk");
    fireEvent.click(within(missing).getByRole("button", { name: /Answer length/ }));

    const row = await screen.findByTestId("field-verbosity");
    expect(within(row).getByRole("radio", { name: "In depth" })).toBeTruthy();
  });

  it("renders a choice value by its label, not its stored token", async () => {
    installFetchMock({
      ...BASE_ROUTES,
      "/api/profile": () => ({
        body: {
          ...PROFILE_OK,
          user: {
            ...PROFILE_OK.user,
            meta: { ...PROFILE_OK.user.meta, communication: { verbosity: "deep-dive" } },
          },
        },
      }),
    });
    renderWithClient(<ProfileView />);

    const row = await screen.findByTestId("field-verbosity");
    expect(row.textContent).toContain("In depth");
    expect(row.textContent).not.toContain("deep-dive");
  });

  it("counts the known details in the header", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    const hero = await screen.findByTestId("profile-hero");
    expect(hero.textContent).toContain("Ruben");
    expect(hero.textContent).toContain("3 / 18");
    await waitFor(() => expect(hero.textContent).toContain("491"));
  });

  it("asks for a name when the file has none", async () => {
    installFetchMock({
      ...BASE_ROUTES,
      "/api/profile": () => ({
        body: { ...PROFILE_OK, user: { ...PROFILE_OK.user, name: null } },
      }),
    });
    renderWithClient(<ProfileView />);

    const hero = await screen.findByTestId("profile-hero");
    expect(within(hero).getByPlaceholderText("Your name")).toBeTruthy();
  });
});

describe("provenance", () => {
  it("shows the date a detail was learned and the sentence behind it", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    const source = await screen.findByTestId("entry-source-primary_language");
    expect(source.textContent).toContain("Learned");

    fireEvent.click(source);
    await screen.findByText("always answer me in German");
  });

  it("shows no date for a detail the audit trail never mentions", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    await screen.findByText("Europe/Berlin");
    expect(screen.queryByTestId("entry-source-timezone")).toBeNull();
  });
});

describe("portrait", () => {
  it("offers to write the portrait when none exists", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    const empty = await screen.findByTestId("portrait-empty");
    expect(empty.textContent).toContain("has not written a portrait");
  });

  it("renders a finished portrait with its feedback", async () => {
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

  it("never shows a portrait that stops mid-word as if it were finished", async () => {
    installFetchMock({
      ...BASE_ROUTES,
      "/api/board/bio": () => ({
        body: {
          text: "I have watched you for 34 days, and while your Tool-",
          generated_at: "2026-09-06T18:00:00Z",
        },
      }),
    });
    renderWithClient(<ProfileView />);

    await screen.findByTestId("portrait-cut-off");
    expect(screen.queryByTestId("portrait-text")).toBeNull();
  });
});

describe("memory and privacy", () => {
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

    await waitFor(() =>
      expect(screen.getByTestId("wiki-empty").textContent).toContain("No page yet"),
    );
  });

  it("opens the source file as a sub-page and comes back", async () => {
    installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    const row = await screen.findByTestId("source-row");
    fireEvent.click(within(row).getByRole("button", { name: /View file/ }));
    await screen.findByTestId("profile-source-markdown");

    fireEvent.click(screen.getByTestId("source-back"));
    await screen.findByTestId("profile-hero");
  });
});

describe("the dead review queue stays deleted", () => {
  it("never calls the review endpoint", async () => {
    const fetchMock = installFetchMock(BASE_ROUTES);
    renderWithClient(<ProfileView />);

    await screen.findByTestId("profile-hero");
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

    await screen.findByText("Profile not available");
    expect(screen.queryByTestId("profile-hero")).toBeNull();
  });
});
