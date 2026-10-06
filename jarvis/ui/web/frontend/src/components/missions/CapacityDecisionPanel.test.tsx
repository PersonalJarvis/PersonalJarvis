import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CapacityDecisionPanel } from "@/components/missions/CapacityDecisionPanel";
import { useI18nStore } from "@/i18n";
import type { MissionState, PaidOffer } from "@/types/missions";

const MISSION_ID = "mission-1";

const OFFER: PaidOffer = {
  provider: "claude-api",
  model: "claude-sonnet-4-6",
  estimated_cost_usd: 1.65,
  cost_cap_usd: 2,
  reason: "provider_quota",
  open_steps: 1,
};

beforeEach(() => {
  useI18nStore.getState().setUi("en", { push: false });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function stubServer(offer: PaidOffer | null) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      return jsonResponse({
        ok: true,
        mission_id: MISSION_ID,
        decision: body.decision,
        state: body.decision === "approve_paid" ? "RUNNING" : "WAITING_CAPACITY",
      });
    }
    return jsonResponse({ mission_id: MISSION_ID, state: "WAITING_CAPACITY", offer });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function posts(fetchMock: ReturnType<typeof stubServer>) {
  return fetchMock.mock.calls
    .filter((call) => call[1]?.method === "POST")
    .map((call) => JSON.parse(String(call[1]?.body)));
}

function renderPanel(state: MissionState | null = "WAITING_CAPACITY") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CapacityDecisionPanel missionId={MISSION_ID} state={state} />
    </QueryClientProvider>,
  );
}

describe("CapacityDecisionPanel", () => {
  it("shows provider, model, estimate, cap and reason before anything is billed", async () => {
    const fetchMock = stubServer(OFFER);
    renderPanel();

    expect(await screen.findByText("claude-api")).toBeTruthy();
    expect(screen.getByText("claude-sonnet-4-6")).toBeTruthy();
    expect(screen.getByText("about $1.65 for 1 open steps")).toBeTruthy();
    expect(screen.getByText("$2.00")).toBeTruthy();
    expect(screen.getByText("Subscription capacity used up")).toBeTruthy();
    expect(posts(fetchMock)).toHaveLength(0);
  });

  it("approves only after an explicit confirmation and echoes the shown offer", async () => {
    const fetchMock = stubServer(OFFER);
    renderPanel();

    await screen.findByText("claude-api"); // the button stays disabled until the offer loads
    fireEvent.click(screen.getByRole("button", { name: "Approve paid API for this mission" }));
    expect(posts(fetchMock)).toHaveLength(0);
    expect(
      screen.getByText("Use claude-api / claude-sonnet-4-6 for this mission, at most $2.00?"),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(posts(fetchMock)).toEqual([
        { decision: "approve_paid", provider: "claude-api", model: "claude-sonnet-4-6" },
      ]),
    );
  });

  it("waits with one click and never sends an offer along", async () => {
    const fetchMock = stubServer(OFFER);
    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Wait" }));
    await waitFor(() =>
      expect(posts(fetchMock)).toEqual([{ decision: "wait", provider: null, model: null }]),
    );
  });

  it("cannot approve when there is no priced paid option", async () => {
    stubServer(null);
    renderPanel();

    expect(await screen.findByText(/No paid API access with a known price/)).toBeTruthy();
    const approve = screen.getByRole("button", { name: "Approve paid API for this mission" });
    expect((approve as HTMLButtonElement).disabled).toBe(true);
  });

  it("renders nothing for a mission that is not waiting", () => {
    const fetchMock = stubServer(OFFER);
    const { container } = renderPanel("RUNNING");
    expect(container.textContent).toBe("");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
