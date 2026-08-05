/**
 * "Checking which wiki mode is active…" must not be the first thing the Wiki
 * tab shows every single time.
 *
 * The probe is honest — the section genuinely does not know which body it owns
 * until the backend answers — but the answer only changes when somebody flips
 * the mode switch, while this component remounts on every navigation away and
 * back. So the wait was being paid over and over for a fact that had not
 * moved. Remembering the last confirmed answer per machine removes the wait
 * without inventing anything: the live probe still overwrites it the moment it
 * disagrees.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { WikiView } from "@/views/WikiView";
import type { WikiTreeResponse } from "@/lib/wikiApi";

const ULTRA_MODE_KEY = "jarvis.wiki.lastUltraMode";

vi.mock("@/hooks/useWikiLive", () => ({
  useWikiLive: () => ({ connected: true, lastEventAt: null }),
}));

vi.mock("@/views/ultrawiki/UltraWikiPanel", () => ({
  UltraWikiPanel: () => <div data-testid="ultrawiki-panel-stub" />,
}));

vi.mock("@/components/wiki/WikiGraph", () => ({
  WikiGraph: () => <div data-testid="wiki-graph-stub" />,
}));

const EMPTY_TREE: WikiTreeResponse = {
  ok: true,
  vault_root: "wiki/obsidian-vault",
  folders: [],
  stats: { total_pages: 3, total_links: 4, last_curator_run: null },
};

/**
 * A backend that never answers the mode probe — the few seconds after a
 * restart, and the case the memory exists for.
 */
function installSilentStatus() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown) => {
      const url = String(input);
      if (url.includes("/api/ultrawiki/status")) {
        return new Response("null", {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url.includes("/api/wiki/tree")) {
        return new Response(JSON.stringify(EMPTY_TREE), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      return new Response("{}", {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
}

function renderWiki() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <WikiView />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.removeItem(ULTRA_MODE_KEY);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.removeItem(ULTRA_MODE_KEY);
});

describe("WikiView — remembering which mode this machine runs", () => {
  it("still waits on a genuinely first-ever visit", async () => {
    installSilentStatus();
    renderWiki();

    expect(await screen.findByTestId("wiki-mode-probe")).toBeDefined();
    expect(screen.queryByTestId("ultrawiki-panel-stub")).toBeNull();
  });

  it("renders Ultra straight away when this machine ran Ultra last time", async () => {
    window.localStorage.setItem(ULTRA_MODE_KEY, "ultra");
    installSilentStatus();
    renderWiki();

    expect(await screen.findByTestId("ultrawiki-panel-stub")).toBeDefined();
    expect(screen.queryByTestId("wiki-mode-probe")).toBeNull();
  });

  it("renders the normal wiki straight away when that is what ran last", async () => {
    window.localStorage.setItem(ULTRA_MODE_KEY, "normal");
    installSilentStatus();
    renderWiki();

    await waitFor(() => {
      expect(screen.queryByTestId("wiki-mode-probe")).toBeNull();
    });
    expect(screen.queryByTestId("ultrawiki-panel-stub")).toBeNull();
    expect(screen.getByTestId("wiki-workspace")).toBeDefined();
  });

  it("lets the backend overrule the memory", async () => {
    window.localStorage.setItem(ULTRA_MODE_KEY, "normal");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: unknown) => {
        const url = String(input);
        if (url.includes("/api/ultrawiki/status")) {
          return new Response(JSON.stringify({ enabled: true, sources: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }
        if (url.includes("/api/wiki/tree")) {
          return new Response(JSON.stringify(EMPTY_TREE), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }
        return new Response("{}", {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );
    renderWiki();

    expect(await screen.findByTestId("ultrawiki-panel-stub")).toBeDefined();
    // ...and the corrected answer is what the next visit starts from.
    await waitFor(() => {
      expect(window.localStorage.getItem(ULTRA_MODE_KEY)).toBe("ultra");
    });
  });
});
