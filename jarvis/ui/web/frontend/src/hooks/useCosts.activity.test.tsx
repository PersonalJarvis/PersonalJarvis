import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { EMPTY_FILTERS, useCostSummary } from "./useCosts";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

test("a retained spend monitor sleeps without removing cached results or polling", async () => {
  const requests: string[] = [];
  vi.stubGlobal("fetch", async (url: string) => {
    requests.push(url);
    return new Response(JSON.stringify({ total_cost: 12 }), { headers: { "Content-Type": "application/json" } });
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  function Monitor({ active }: { active: boolean }) {
    const summary = useCostSummary(EMPTY_FILTERS, active);
    return <span>{summary.data ? "cached" : "empty"}</span>;
  }
  const tree = (active: boolean) => <QueryClientProvider client={client}><Monitor active={active} /></QueryClientProvider>;
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  const view = render(tree(false));
  expect(requests).toEqual([]);
  view.rerender(tree(true));
  await waitFor(() => expect(view.getByText("cached")).toBeTruthy());
  expect(requests).toHaveLength(1);

  view.rerender(tree(false));
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["costs", "summary"] });
    vi.advanceTimersByTime(240_000);
  });
  expect(requests).toHaveLength(1);
  expect(view.getByText("cached")).toBeTruthy();

  vi.useRealTimers();
  view.rerender(tree(true));
  await waitFor(() => expect(requests).toHaveLength(2));
  view.unmount();
  client.clear();
});
