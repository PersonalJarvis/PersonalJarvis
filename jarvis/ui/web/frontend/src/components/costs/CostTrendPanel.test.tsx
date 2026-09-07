import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EMPTY_FILTERS, type CostBucket, type CostFilters, type CostSummary } from "@/hooks/useCosts";
import { useI18nStore } from "@/i18n";
import { CostTrendPanel } from "./CostTrendPanel";
import { roleColor } from "./costFormat";
import { CostsView } from "@/views/CostsView";

class ChartResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const clients: QueryClient[] = [];

beforeEach(() => {
  useI18nStore.getState().setUi("en", { push: false });
  vi.stubGlobal("ResizeObserver", ChartResizeObserver);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 640, height: 200, x: 0, y: 0, top: 0, left: 0, right: 640, bottom: 200,
    toJSON: () => ({}),
  });
});

afterEach(() => {
  cleanup();
  for (const client of clients.splice(0)) client.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function bucket(breakdown: Record<string, number>, tokens = 100): CostBucket {
  return {
    key: "2026-09-07", breakdown,
    cost_usd: Object.values(breakdown).reduce((sum, cost) => sum + cost, 0),
    tokens_in: tokens, tokens_out: 20, tokens_cached: 10, tokens_total: tokens + 30,
    entries: 3, gap_tokens: 0, subscription_usd: 0, chars: 0, audio_ms: 0,
    price_sources: ["recorded"], last_ts_ms: 0, cost_share: 1, token_share: 1, members: [],
  };
}

function summary(point: CostBucket): CostSummary {
  return {
    since_ms: 1, until_ms: 2, bucket: "day",
    totals: {
      ...point, gap_entries: 0, free_tokens: 0, estimated_usd: 0, first_ts_ms: 1,
    },
    series: [point], by_provider: [], by_model: [], by_role: [], by_surface: [],
    top_refs: [], models: [], refs_total: 0, sources_present: [],
    currency: { eur_per_usd: 0.9, source: "default" },
    facets: {
      providers: [], models: [], roles: ["agent", "worker", "tts"],
      surfaces: ["agentic-ide", "agent-chat", "mission", "jarvis-voice"],
    },
  };
}

function mount(data: CostSummary, filters: CostFilters = EMPTY_FILTERS) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <CostTrendPanel filters={filters} summary={data} loading={false}
        title="Over time" subtitle="September" currency="usd" eurPerUsd={0.9} />
    </QueryClientProvider>,
  );
}

function maxAxisTick(container: HTMLElement) {
  const ticks = container.querySelectorAll('.recharts-cartesian-axis-tick-value[orientation="left"]');
  if (!ticks.length) return NaN;
  return Math.max(...Array.from(ticks, (tick) => {
    const text = tick.textContent ?? "";
    const multiplier = text.endsWith("M") ? 1_000_000 : text.endsWith("k") ? 1000 : 1;
    return parseFloat(text.replace(/[$,]/g, "")) * multiplier;
  }));
}

describe("CostTrendPanel", () => {
  it("excludes IDE surfaces, rescales by orders of magnitude, and restores original totals", async () => {
    const all = summary(bucket({ agent: 100_000, worker: 20_000, tts: 0.5 }, 1_000_000));
    const filtered = summary(bucket({ agent: 0.2, worker: 0.1, tts: 0.5 }));
    const original = JSON.stringify(all);
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => filtered });
    vi.stubGlobal("fetch", fetcher);
    const { container } = mount(all, { ...EMPTY_FILTERS, providers: ["example"], billing: "billed" });
    await waitFor(() => expect(maxAxisTick(container)).toBeGreaterThan(100_000));
    expect(fetcher).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("checkbox", { name: "Include Agentic IDE" }));
    await waitFor(() => expect(maxAxisTick(container)).toBeLessThan(2));
    const params = new URL(String(fetcher.mock.calls[0][0]), "http://localhost").searchParams;
    expect(params.getAll("surface")).toEqual(["agent-chat", "mission", "jarvis-voice"]);
    expect(params.getAll("provider")).toEqual(["example"]);
    expect(params.get("billing")).toBe("billed");
    // Non-IDE agent/worker spend survives and keeps the same palette.
    expect(container.querySelector(`path[fill="${roleColor("agent")}"]`)).not.toBeNull();
    expect(container.querySelector(`path[fill="${roleColor("worker")}"]`)).not.toBeNull();
    expect(JSON.stringify(all)).toBe(original);
    fireEvent.keyDown(screen.getByRole("application"), { key: "ArrowRight" });
    await waitFor(() => expect(container.querySelector(".recharts-tooltip-wrapper")?.textContent).toContain("Total$0.80"));
    expect(screen.queryByText("$100,000")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Tokens" }));
    await waitFor(() => expect(maxAxisTick(container)).toBeLessThan(1000));
    fireEvent.click(screen.getByRole("checkbox", { name: "Include Agentic IDE" }));
    await waitFor(() => expect(maxAxisTick(container)).toBeGreaterThanOrEqual(1_000_000));
    expect(JSON.stringify(all)).toBe(original);
  });

  it("never widens an IDE-only area to all surfaces", () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    mount(summary(bucket({ agent: 100_000 })), { ...EMPTY_FILTERS, surfaces: ["agentic-ide"] });
    fireEvent.click(screen.getByRole("checkbox", { name: "Include Agentic IDE" }));
    expect(screen.getByText("Nothing spent in this range.")).toBeTruthy();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("keeps hour, provider and explicit surface filters and drops absent legend entries", async () => {
    const data = summary(bucket({ agent: 100_000, tts: 0.5 }));
    const filtered = summary(bucket({ tts: 0.5, agent: 0 }));
    filtered.bucket = "hour";
    filtered.series[0].key = "2026-09-07T12:00";
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => filtered });
    vi.stubGlobal("fetch", fetcher);
    const { container } = mount(data, {
      ...EMPTY_FILTERS, surfaces: ["agentic-ide", "jarvis-voice"],
      sinceMs: 1000, untilMs: 2000, providers: ["example"],
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "Include Agentic IDE" }));
    await waitFor(() => expect(screen.getByText("Text to speech")).toBeTruthy());
    expect(screen.queryByText("Coding agent")).toBeNull();
    const params = new URL(String(fetcher.mock.calls[0][0]), "http://localhost").searchParams;
    expect(params.getAll("surface")).toEqual(["jarvis-voice"]);
    expect(params.getAll("provider")).toEqual(["example"]);
    expect(params.get("since_ms")).toBe("1000");
    expect(params.get("until_ms")).toBe("2000");
    fireEvent.keyDown(screen.getByRole("application"), { key: "ArrowRight" });
    await waitFor(() => expect(container.querySelector(".recharts-tooltip-wrapper")?.textContent).toContain("Total$0.50"));
  });

  it("shows loading and errors instead of old coding spend under an active filter", async () => {
    let reject!: (error: Error) => void;
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise((_resolve, fail) => { reject = fail; })));
    const { container } = mount(summary(bucket({ agent: 100_000 })));
    fireEvent.click(screen.getByRole("checkbox", { name: "Include Agentic IDE" }));
    expect(screen.getByRole("status")).toBeTruthy();
    expect(container.querySelector(".recharts-bar")).toBeNull();
    reject(new Error("offline"));
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(container.querySelector(".recharts-bar")).toBeNull();
  });

  it("refreshes the active filtered chart from the Spend view refresh button", async () => {
    const all = summary(bucket({ agent: 100_000, tts: 0.5 }));
    let filtered = summary(bucket({ tts: 0.5 }));
    let filteredReads = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: string) => {
      const url = new URL(input, "http://localhost");
      let data: unknown = { days: [], currency: all.currency };
      if (url.pathname.endsWith("/summary")) {
        const excludesIde = url.searchParams.has("surface");
        if (excludesIde) filteredReads += 1;
        data = excludesIde ? filtered : all;
      }
      return { ok: true, json: async () => data };
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    clients.push(client);
    const { container } = render(<QueryClientProvider client={client}><CostsView /></QueryClientProvider>);
    await waitFor(() => expect(maxAxisTick(container)).toBeGreaterThan(100_000));
    fireEvent.click(screen.getByRole("checkbox", { name: "Include Agentic IDE" }));
    await waitFor(() => expect(maxAxisTick(container)).toBeLessThan(2));
    filtered = summary(bucket({ tts: 50 }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(maxAxisTick(container)).toBeGreaterThanOrEqual(50));
    expect(filteredReads).toBe(2);
  });
});
