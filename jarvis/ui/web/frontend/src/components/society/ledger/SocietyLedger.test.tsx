import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { SocietyLedger, ledgerCost, ledgerTotalCost } from "./SocietyLedger";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderLedger() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  return render(
    <QueryClientProvider client={client}>
      <SocietyLedger />
    </QueryClientProvider>,
  );
}

it("renders the durable society event history newest first with cost", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/costs/summary")) {
      return {
        ok: true,
        json: async () => ({ totals: { cost_usd: 0.3142 } }),
      };
    }
    return {
      ok: true,
      json: async () => ({
        events: [
        {
          seq: 1,
          event_id: "open",
          msg_type: "ROOM_OPEN",
          from_agent: "jarvis",
          to_agent: null,
          trace_id: "room:r",
          parent_event_id: null,
          ts_ms: 1000,
          cost_usd: 0,
          payload: { text: "Pick a provider" },
        },
        {
          seq: 2,
          event_id: "say",
          msg_type: "SAY",
          from_agent: "scout",
          to_agent: null,
          trace_id: "room:r",
          parent_event_id: null,
          ts_ms: 2000,
          cost_usd: 0.0042,
          payload: { text: "Use the smaller one" },
        },
        ],
      }),
    };
  }) as unknown as typeof fetch;
  vi.stubGlobal("fetch", fetchMock);

  renderLedger();

  await waitFor(() => expect(screen.getByText("Use the smaller one")).toBeTruthy());
  const rows = screen.getAllByRole("row");
  expect(rows[1].textContent).toContain("SAY");
  expect(rows[1].textContent).toContain("$0.0042");
  expect(rows[2].textContent).toContain("ROOM_OPEN");
  expect(screen.getByTestId("society-ledger-total-cost").textContent).toContain("$0.31");
  expect(fetchMock).toHaveBeenCalledWith("/api/society/events?limit=200", { cache: "no-store" });
  expect(fetchMock).toHaveBeenCalledWith("/api/costs/summary?days=0&surface=society", {
    cache: "no-store",
  });
});

it("keeps sub-cent spend visible instead of rounding it to zero", () => {
  expect(ledgerCost(0)).toBe("—");
  expect(ledgerCost(0.0042)).toBe("$0.0042");
  expect(ledgerCost(0.2)).toBe("$0.20");
  expect(ledgerTotalCost(0)).toBe("$0.00");
  expect(ledgerTotalCost(0.0042)).toBe("$0.0042");
});
