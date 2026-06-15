/**
 * Outputs view — Continue/Restart button visibility per mission status.
 *
 * Gating contract:
 * - status "cancelled" + a mission_id → a "Continue" button.
 * - status "error"     + a mission_id → a "Restart" button.
 * - status "running" / "success" / no mission_id → neither.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { OutputsView } from "@/views/OutputsView";
import type { OutputSummary } from "@/hooks/useOutputs";

// ViewHeader pulls in ChatsView, which subscribes to a WS client; null keeps
// that effect a deterministic no-op in jsdom (same pattern as ClisView.test).
vi.mock("@/hooks/useWebSocket", () => ({
  getWSClient: () => null,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function installFetchMock(sessions: OutputSummary[]) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/artifacts")) {
      return { ok: true, status: 200, json: async () => ({ files: [] }) };
    }
    if (url.includes("/plan")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ plan: null, steps: [] }),
      };
    }
    if (url.includes("/api/outputs")) {
      return { ok: true, status: 200, json: async () => ({ sessions }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <OutputsView />
    </QueryClientProvider>,
  );
}

function session(over: Partial<OutputSummary>): OutputSummary {
  return {
    slug: "20260615T120000__task__abcdef123456",
    utterance: "Some task",
    status: "unknown",
    mission_id: "mission-1",
    started_at: 1_750_000_000,
    ...over,
  };
}

describe("OutputsView rerun button gating", () => {
  it("shows Continue (and no Restart) for a cancelled mission", async () => {
    installFetchMock([
      session({ slug: "cancelled-slug", status: "cancelled", mission_id: "m-c" }),
    ]);
    renderView();
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: "Continue" }).length,
      ).toBeGreaterThan(0),
    );
    expect(screen.queryByRole("button", { name: "Restart" })).toBeNull();
  });

  it("shows Restart (and no Continue) for a failed mission", async () => {
    installFetchMock([
      session({ slug: "error-slug", status: "error", mission_id: "m-e" }),
    ]);
    renderView();
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: "Restart" }).length,
      ).toBeGreaterThan(0),
    );
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
  });

  it("shows neither for running or successful missions", async () => {
    installFetchMock([
      session({ slug: "run-slug", status: "running", mission_id: "m-r" }),
      session({ slug: "ok-slug", status: "success", mission_id: "m-ok" }),
    ]);
    renderView();
    // Let the list settle: the running row renders a hold-to-abort control
    // (there may be more than one — the auto-selected detail pane too).
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: "Hold to abort" }).length,
      ).toBeGreaterThan(0),
    );
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Restart" })).toBeNull();
  });
});
