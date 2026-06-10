/**
 * Component tests for ProfileView's Review-Queue section.
 *
 * Regression guard for the "red error badge for an intentionally-disabled
 * Curator" bug: the legacy Curator-Merger is soft-disabled by design
 * (`memory.legacy_curator.enabled = false`, since 2026-05-17), so
 * `GET /api/profile/reviews` returns HTTP 503. The backend contract
 * (`jarvis/ui/web/profile_routes.py` module docstring) requires the UI to
 * render a friendly empty-state for that 503 — NOT a destructive red badge.
 *
 * The top-level `ErrorState` already honored this; the nested `ReviewsSection`
 * did not. These tests pin the contract for the nested section.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ProfileView } from "@/views/ProfileView";

// RawMarkdownSection subscribes to a WS client in a useEffect; null keeps the
// effect a deterministic no-op in jsdom.
vi.mock("@/hooks/useWebSocket", () => ({
  getWSClient: () => null,
}));

function freshClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
    },
  });
}

function renderWithClient(node: React.ReactNode) {
  const client = freshClient();
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
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
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return fetchMock;
}

const PROFILE_OK = {
  user: { name: "Alex", meta: {}, path: "data/workspace/USER.md" },
  people: [],
  reviews_count: 0,
};

const RAW_OK = {
  content: "",
  path: "data/workspace/USER.md",
  mtime_ms: null,
  size_bytes: 0,
};

const CURATOR_503_DETAIL =
  "Der Curator laeuft in dieser Session nicht — evtl. Mock-Brain oder ein " +
  "Provider ohne Memory-Integration.";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ProfileView — Review-Queue with a disabled Curator (503)", () => {
  it("renders a friendly disabled-state, not a destructive red badge", async () => {
    installFetchMock({
      "/api/profile/reviews": () => ({
        status: 503,
        body: { detail: CURATOR_503_DETAIL },
      }),
      "/api/profile/raw": () => ({ body: RAW_OK }),
      "/api/profile": () => ({ body: PROFILE_OK }),
    });

    renderWithClient(<ProfileView />);

    // The friendly, non-alarming disabled card appears.
    await waitFor(() => {
      expect(screen.getByTestId("reviews-disabled")).toBeDefined();
    });

    // The destructive red badge must NOT be rendered for an expected 503.
    expect(screen.queryByTestId("reviews-error")).toBeNull();

    // The raw backend message is not surfaced verbatim as an error.
    expect(screen.queryByText(CURATOR_503_DETAIL)).toBeNull();
  });
});

describe("ProfileView — Review-Queue with a genuine server error (500)", () => {
  it("still renders the destructive badge for non-503 failures", async () => {
    installFetchMock({
      "/api/profile/reviews": () => ({ status: 500, body: { detail: "boom" } }),
      "/api/profile/raw": () => ({ body: RAW_OK }),
      "/api/profile": () => ({ body: PROFILE_OK }),
    });

    renderWithClient(<ProfileView />);

    await waitFor(() => {
      expect(screen.getByTestId("reviews-error")).toBeDefined();
    });
    expect(screen.queryByTestId("reviews-disabled")).toBeNull();
  });
});

// Open knowledge matrix: the user found the "classified dossier" treatment
// (diagonal-hatch redaction bars on unknown fields + a "Confidential" hero)
// misleading — it read as information being withheld. These tests pin the
// de-classified contract: empty fields are plainly readable, nothing is
// concealed, and the secrecy framing is gone. PROFILE_OK has `meta: {}`, so
// every cluster field is empty — the worst case for the old redaction bars.
describe("ProfileView — open knowledge matrix (no concealment)", () => {
  it("renders unknown fields as readable text, never as a redaction bar", async () => {
    installFetchMock({
      "/api/profile/reviews": () => ({
        status: 503,
        body: { detail: CURATOR_503_DETAIL },
      }),
      "/api/profile/raw": () => ({ body: RAW_OK }),
      "/api/profile": () => ({ body: PROFILE_OK }),
    });

    const { container } = renderWithClient(<ProfileView />);

    // The knowledge matrix renders unknown fields as the plain "not known yet"
    // label instead of a black hatch bar.
    await waitFor(() => {
      expect(screen.getAllByText("not known yet").length).toBeGreaterThan(0);
    });

    // Core contract: empty fields must NOT be concealed behind a redaction bar.
    expect(container.querySelector(".dossier-redact")).toBeNull();
  });

  it("drops the classified-dossier framing from the hero", async () => {
    installFetchMock({
      "/api/profile/reviews": () => ({
        status: 503,
        body: { detail: CURATOR_503_DETAIL },
      }),
      "/api/profile/raw": () => ({ body: RAW_OK }),
      "/api/profile": () => ({ body: PROFILE_OK }),
    });

    const { container } = renderWithClient(<ProfileView />);

    // The hero leads with a conversational, named headline — not a
    // classification strip.
    await waitFor(() => {
      expect(screen.getByText("A fresh page, Alex.")).toBeDefined();
    });

    // The secrecy signals are gone.
    expect(screen.queryByText("Confidential")).toBeNull();
    expect(container.querySelector(".dossier-hatch")).toBeNull();
  });
});

// The "Knows you" treatment: a blank profile must read as an invitation, not
// as a wall of grey "not known yet" repetitions. These tests pin the pillars:
// a named acquaintance stage instead of a bare percentage, ONE rotating
// question card with a speakable prompt, and the knowledge wave.
describe("ProfileView — the knows-you treatment for a blank profile", () => {
  function mockBlankProfile() {
    installFetchMock({
      "/api/profile/reviews": () => ({
        status: 503,
        body: { detail: CURATOR_503_DETAIL },
      }),
      "/api/profile/raw": () => ({ body: RAW_OK }),
      "/api/profile": () => ({ body: PROFILE_OK }),
    });
  }

  it("names the acquaintance stage instead of leading with a bare percent", async () => {
    mockBlankProfile();
    renderWithClient(<ProfileView />);

    await waitFor(() => {
      expect(screen.getAllByText(/A blank page/).length).toBeGreaterThan(0);
    });
  });

  it("shows one open question with a speakable prompt and rotates on Next", async () => {
    mockBlankProfile();
    renderWithClient(<ProfileView />);

    // The top-priority open field (name) surfaces as the current question…
    await waitFor(() => {
      expect(
        screen.getByText("What name shall I write on the first page?"),
      ).toBeDefined();
    });
    // …and carries the literal sentence the user can speak to Jarvis.
    expect(screen.getAllByText(/Just say/).length).toBeGreaterThan(0);

    // Clicking Next advances to the second-priority question.
    fireEvent.click(screen.getByTestId("ask-next"));
    await waitFor(() => {
      expect(
        screen.getByText("How would you like to be addressed?"),
      ).toBeDefined();
    });
  });

  it("summarizes progress as one quiet text line, not metric tiles", async () => {
    mockBlankProfile();
    renderWithClient(<ProfileView />);

    // The open hero carries a single inline summary sentence…
    await waitFor(() => {
      expect(screen.getByText("0 of 18 things learned")).toBeDefined();
    });
    // …and the decorative waveform is gone for good.
    expect(screen.queryByTestId("knowledge-wave")).toBeNull();
  });
});
