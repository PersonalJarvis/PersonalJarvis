import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

vi.mock("@/store/events", () => ({
  useEventStore: (sel: (s: { events: unknown[] }) => unknown) =>
    sel({ events: [] }),
}));

vi.mock("@/components/runs/api", () => ({
  fetchRuns: vi.fn(async () => [
    {
      session_id: "s1",
      started_ms: 1,
      ended_ms: 2,
      duration_s: 0.001,
      hangup_reason: "idle_timeout",
      wake_source: "voice",
      turn_count: 0,
      total_cost_usd: 0,
      error_count: 0,
      slo_status: "ok",
      preview: "hi",
    },
  ]),
  fetchRunDetail: vi.fn(),
}));

import { useRuns } from "@/hooks/useRuns";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useRuns", () => {
  beforeEach(() => vi.clearAllMocks());
  it("loads the runs list", async () => {
    const { result } = renderHook(() => useRuns(), { wrapper });
    await waitFor(() => expect(result.current.data?.length).toBe(1));
    expect(result.current.data?.[0].session_id).toBe("s1");
  });
});
