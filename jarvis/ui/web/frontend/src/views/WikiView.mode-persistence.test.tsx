/**
 * WikiView — the Wiki mode has to survive a restart (regression).
 *
 * Two failures shared one root: an UNANSWERED status was read as "Ultra is
 * off". After the in-app Restart the window is back before the backend is,
 * the poll-safe fetcher swallows that as a successful `null`, and with no
 * retry and no interval the section stayed on the normal wiki for the rest of
 * the session — on installs whose config said `[ultrawiki] enabled = true`.
 * Clicking Ultra then reopened the ONE-TIME activation wizard, because the
 * same empty answer also carried no configured embedding slot.
 *
 * These tests pin the honest behavior: unknown renders NEITHER body, keeps
 * asking, and lands on the mode the backend actually reports.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { WikiView } from "@/views/WikiView";
import type { WikiTreeResponse } from "@/lib/wikiApi";
import type { UltraWikiStatus } from "@/lib/ultrawikiApi";

vi.mock("@/hooks/useWikiLive", () => ({
  useWikiLive: () => ({ connected: true, lastEventAt: null }),
}));

vi.mock("@/views/ultrawiki/UltraWikiPanel", () => ({
  UltraWikiPanel: () => <div data-testid="ultrawiki-panel-stub" />,
}));

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

const EMPTY_TREE: WikiTreeResponse = {
  ok: true,
  vault_root: "wiki/obsidian-vault",
  folders: [],
  stats: { total_pages: 0, total_links: 0, last_curator_run: null },
};

function ultraStatus(enabled: boolean): UltraWikiStatus {
  return {
    enabled,
    configured: true,
    started: enabled,
    db_backend: "sqlite",
    backend_in_use: enabled ? "sqlite" : "",
    slots: {
      embedding: {
        provider: "gemini",
        model: "gemini-embedding-001",
        ready: true,
        reason: "",
      },
    },
    counts: {},
    pipeline: { running: false, processed: {} },
    sources: [],
    jobs: [],
    search_legs: { keyword: { available: true } },
    degradations: [],
  };
}

/**
 * `statusAnswer` is read per request, so a test can have the backend answer
 * with a 503 first (still booting) and a real payload afterwards.
 */
function installFetchMock(statusAnswer: () => UltraWikiStatus | "unavailable") {
  const routes: Record<string, () => unknown> = {
    "/api/wiki/tree": () => EMPTY_TREE,
    "/api/wiki/health": () => ({ ok: false }),
    "/api/setup/obsidian/status": () => ({
      installed: true,
      config_exists: true,
      vault_registered: true,
      recommended_action: "ok",
    }),
    "/api/setup/state": () => ({ obsidian_setup_seen: true }),
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/ultrawiki/status")) {
      const answer = statusAnswer();
      if (answer === "unavailable") {
        // Exactly what a still-booting backend gives the window: not an
        // exception the caller can see, just an unusable response.
        return { ok: false, status: 503, statusText: "Service Unavailable" } as Response;
      }
      return { ok: true, status: 200, statusText: "OK", json: async () => answer } as Response;
    }
    const prefixes = Object.keys(routes).sort((a, b) => b.length - a.length);
    for (const prefix of prefixes) {
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
  vi.useRealTimers();
});

describe("WikiView — the mode survives a restart", () => {
  it("does NOT fall back to the normal wiki while the status is unknown", async () => {
    installFetchMock(() => "unavailable");
    renderWithClient(<WikiView />);

    await waitFor(() => {
      expect(screen.getByTestId("wiki-mode-probe")).toBeDefined();
    });
    // The bug: the normal wiki's own body rendered here, on an Ultra install.
    expect(screen.queryByTestId("wiki-empty-state")).toBeNull();
    expect(screen.queryByTestId("wiki-health-strip")).toBeNull();
    expect(screen.queryByTestId("ultrawiki-panel-stub")).toBeNull();
  });

  it("lands on Ultra once the backend answers, without a reload", async () => {
    let booting = true;
    installFetchMock(() => (booting ? "unavailable" : ultraStatus(true)));
    renderWithClient(<WikiView />);

    await waitFor(() => {
      expect(screen.getByTestId("wiki-mode-probe")).toBeDefined();
    });

    // The backend finishes starting; the section must reach Ultra on its own.
    booting = false;
    await waitFor(
      () => {
        expect(screen.getByTestId("ultrawiki-panel-stub")).toBeDefined();
      },
      { timeout: 8_000 },
    );
    expect(screen.queryByTestId("wiki-mode-probe")).toBeNull();
    expect(
      screen.getByTestId("wiki-mode-toggle").getAttribute("data-mode"),
    ).toBe("ultra");
  }, 12_000);

  it("keeps the confirmed mode when a later poll comes back empty", async () => {
    let answered = false;
    installFetchMock(() => {
      if (!answered) {
        answered = true;
        return ultraStatus(true);
      }
      return "unavailable";
    });
    renderWithClient(<WikiView />);

    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-panel-stub")).toBeDefined();
    });

    // A blip must not flip the section back to the normal wiki behind the
    // user's back — the last CONFIRMED answer stands.
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(screen.getByTestId("ultrawiki-panel-stub")).toBeDefined();
    expect(screen.queryByTestId("wiki-empty-state")).toBeNull();
  });

  it("renders the normal wiki when the backend actually says the mode is off", async () => {
    installFetchMock(() => ultraStatus(false));
    renderWithClient(<WikiView />);

    await waitFor(() => {
      expect(screen.getByTestId("wiki-empty-state")).toBeDefined();
    });
    expect(screen.queryByTestId("wiki-mode-probe")).toBeNull();
    expect(screen.queryByTestId("ultrawiki-panel-stub")).toBeNull();
  });
});
