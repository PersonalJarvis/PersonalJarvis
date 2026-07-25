/**
 * WikiView Ultra-mode toggle tests (decision D-5, either-or).
 *
 * Pins that the Normal | Ultra segmented toggle renders at the top of the
 * Wiki section and that `GET /api/ultrawiki/status` → `enabled` is the one
 * source of truth for which body is mounted: normal wiki content vs the
 * (lazy) UltraWikiPanel. The panel itself is stubbed — its behavior has its
 * own colocated tests.
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

// The Ultra panel is lazy-loaded by WikiView; stub it so this test pins the
// mode switch alone (the panel has its own tests).
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
    started: enabled,
    db_backend: "sqlite",
    backend_in_use: enabled ? "sqlite" : "",
    slots: {},
    counts: {},
    pipeline: { running: false, processed: {} },
    sources: [],
    jobs: [],
    search_legs: { keyword: { available: true } },
    degradations: [],
  };
}

function installFetchMock(routes: Record<string, () => unknown>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
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

function baseRoutes(enabled: boolean): Record<string, () => unknown> {
  return {
    "/api/wiki/tree": () => EMPTY_TREE,
    "/api/wiki/health": () => ({ ok: false }),
    "/api/ultrawiki/status": () => ultraStatus(enabled),
    "/api/setup/obsidian/status": () => ({
      installed: true,
      config_exists: true,
      vault_registered: true,
      recommended_action: "ok",
    }),
    "/api/setup/state": () => ({ obsidian_setup_seen: true }),
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WikiView — Ultra mode toggle", () => {
  it("renders the Normal|Ultra toggle and the normal wiki body while disabled", async () => {
    installFetchMock(baseRoutes(false));
    renderWithClient(<WikiView />);

    await waitFor(() => {
      expect(screen.getByTestId("wiki-mode-toggle")).toBeDefined();
    });
    await waitFor(() => {
      expect(
        screen.getByTestId("wiki-mode-toggle").getAttribute("data-mode"),
      ).toBe("normal");
    });

    // Normal body mounted, Ultra panel absent.
    await waitFor(() => {
      expect(screen.getByTestId("wiki-empty-state")).toBeDefined();
    });
    expect(screen.queryByTestId("ultrawiki-panel-stub")).toBeNull();

    const normalButton = screen.getByTestId("wiki-mode-normal");
    const ultraButton = screen.getByTestId("wiki-mode-ultra");
    expect(normalButton.getAttribute("aria-pressed")).toBe("true");
    expect(ultraButton.getAttribute("aria-pressed")).toBe("false");
  });

  it("mounts the Ultra panel INSTEAD of the normal wiki body when status.enabled is true", async () => {
    installFetchMock(baseRoutes(true));
    renderWithClient(<WikiView />);

    await waitFor(() => {
      expect(
        screen.getByTestId("wiki-mode-toggle").getAttribute("data-mode"),
      ).toBe("ultra");
    });

    // The lazy chunk resolves and replaces the normal body entirely (D-5).
    await waitFor(() => {
      expect(screen.getByTestId("ultrawiki-panel-stub")).toBeDefined();
    });
    expect(screen.queryByTestId("wiki-empty-state")).toBeNull();
    expect(screen.queryByTestId("wiki-health-strip")).toBeNull();

    expect(
      screen.getByTestId("wiki-mode-ultra").getAttribute("aria-pressed"),
    ).toBe("true");
  });
});
