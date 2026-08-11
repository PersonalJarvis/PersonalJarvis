/**
 * The plan of a RUNNING run is a live step timeline — it must keep itself
 * fresh without anyone pressing Refresh (the Visualization canvas is "the
 * thing on the second monitor while the mission is still going"). The plan
 * of a finished run is history and must never poll: three hundred archived
 * runs re-reading their transcripts every few seconds would keep a disk busy
 * for data that can no longer change.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import {
  usePlanForOutput,
  type OutputSummary,
  type PlanResponse,
} from "@/hooks/useOutputs";

const EMPTY_PLAN: PlanResponse = { plan: null, steps: [] };

function planFetchMock() {
  return vi.fn(async (input: RequestInfo | URL) => {
    if (!String(input).includes("/plan")) throw new Error(`unexpected ${input}`);
    return { ok: true, status: 200, json: async () => EMPTY_PLAN };
  });
}

function renderPlanHook(runs: OutputSummary[], slug: string) {
  // gcTime stays realistic: in the app the outputs-list observer keeps the
  // cache entry alive; gcTime 0 here would garbage-collect the seeded list
  // before the interval callback could ever read it.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 60_000 } },
  });
  // The outputs list is what every view already fetches and caches; the plan
  // hook only READS it to decide whether its run is still alive.
  client.setQueryData(["outputs"], runs);
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => usePlanForOutput(slug), { wrapper });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("usePlanForOutput live-follow", () => {
  it("keeps refetching the plan while its run is running", async () => {
    const fetchMock = planFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = renderPlanHook(
      [{ slug: "run-live", status: "running" }],
      "run-live",
    );

    await vi.advanceTimersByTimeAsync(50);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // Two poll intervals later the timeline has been re-read twice more.
    await vi.advanceTimersByTimeAsync(6_200);
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(3);
    unmount();
  });

  it("never polls the plan of a finished run", async () => {
    const fetchMock = planFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = renderPlanHook(
      [{ slug: "run-done", status: "success" }],
      "run-done",
    );

    await vi.advanceTimersByTimeAsync(50);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // Long after any poll interval would have fired: still only the one read.
    await vi.advanceTimersByTimeAsync(20_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    unmount();
  });

  it("treats a run missing from the list as finished (no poll)", async () => {
    const fetchMock = planFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = renderPlanHook([], "run-gone");

    await vi.advanceTimersByTimeAsync(50);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    unmount();
  });
});
