/**
 * SourcesPanel tests — the consent gate.
 *
 * Pins that Approve is an explicit POST to the approve route and that a
 * pending source exposes NO sync control at all (the backend would refuse
 * the sync; the UI must not dangle a dead button).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { SourcesPanel } from "@/components/ultrawiki/SourcesPanel";
import { useEventStore } from "@/store/events";
import type { UltraWikiSource } from "@/lib/ultrawikiApi";

function renderWithClient(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

function installFetchMock(
  routes: Record<string, (init?: RequestInit) => unknown>,
) {
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const prefixes = Object.keys(routes).sort((a, b) => b.length - a.length);
      for (const prefix of prefixes) {
        if (url.startsWith(prefix)) {
          return {
            ok: true,
            status: 200,
            statusText: "OK",
            json: async () => routes[prefix](init),
          } as Response;
        }
      }
      throw new Error(`unexpected fetch ${url}`);
    },
  );
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return fetchMock;
}

function source(overrides: Partial<UltraWikiSource>): UltraWikiSource {
  return {
    id: "src-x",
    connector: "local-folder",
    label: "Some folder",
    consent: "pending",
    enabled: true,
    areas: [],
    counts: {
      captured: 1,
      keyword_indexed: 2,
      embedded: 3,
      distilled: 4,
      failed: 0,
      total: 10,
    },
    sync_state: null,
    last_sync_at: null,
    last_error: null,
    ...overrides,
  };
}

const PENDING = source({ id: "src-pending", label: "Pending folder" });
const APPROVED = source({
  id: "src-approved",
  label: "Approved folder",
  consent: "approved",
  last_sync_at: "2026-07-20T10:00:00Z",
});

beforeEach(() => {
  // Brand discipline: pin an arbitrary assistant name, never the host config.
  useEventStore.getState().setAssistantName("Nova");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SourcesPanel — consent gate", () => {
  it("fires the approve POST for a pending source and reports the change", async () => {
    const fetchMock = installFetchMock({
      "/api/ultrawiki/sources/src-pending/approve": () => ({
        ...PENDING,
        consent: "approved",
      }),
    });
    const onChanged = vi.fn();
    renderWithClient(
      <SourcesPanel sources={[PENDING, APPROVED]} onChanged={onChanged} />,
    );

    fireEvent.click(screen.getByTestId("uw-source-approve-src-pending"));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/ultrawiki/sources/src-pending/approve",
        expect.objectContaining({ method: "POST" }),
      );
    });
    await waitFor(() => {
      expect(onChanged).toHaveBeenCalledTimes(1);
    });
  });

  it("shows NO sync control on a pending source, but does on an approved one", () => {
    installFetchMock({});
    renderWithClient(
      <SourcesPanel sources={[PENDING, APPROVED]} onChanged={vi.fn()} />,
    );

    // Pending: consent badge, approve button + scope description, no sync.
    expect(
      screen
        .getByTestId("ultrawiki-consent-src-pending")
        .getAttribute("data-consent"),
    ).toBe("pending");
    expect(screen.getByTestId("uw-source-approve-src-pending")).toBeDefined();
    expect(screen.queryByTestId("uw-source-sync-src-pending")).toBeNull();

    // Approved: sync + revoke, no approve.
    expect(screen.getByTestId("uw-source-sync-src-approved")).toBeDefined();
    expect(screen.getByTestId("uw-source-revoke-src-approved")).toBeDefined();
    expect(screen.queryByTestId("uw-source-approve-src-approved")).toBeNull();
  });

  it("starts a sync for an approved source via the sync route", async () => {
    const fetchMock = installFetchMock({
      "/api/ultrawiki/sources/src-approved/sync": () => ({
        job_id: "job-1",
        status: "queued",
        source_id: "src-approved",
      }),
    });
    const onChanged = vi.fn();
    renderWithClient(
      <SourcesPanel sources={[APPROVED]} onChanged={onChanged} />,
    );

    fireEvent.click(screen.getByTestId("uw-source-sync-src-approved"));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/ultrawiki/sources/src-approved/sync",
        expect.objectContaining({ method: "POST" }),
      );
    });
    await waitFor(() => {
      expect(onChanged).toHaveBeenCalledTimes(1);
    });
  });
});
