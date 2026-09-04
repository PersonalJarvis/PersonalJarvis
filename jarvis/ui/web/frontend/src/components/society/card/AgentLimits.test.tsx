/**
 * The card's Limits block — the budget, ceiling and concurrency a person can
 * change after the agent exists.
 *
 * All three were settable only once, at creation, behind a disclosure labelled
 * "More" that nobody opened, so the numbers on the card read as decoration
 * (maintainer, 2026-09-03). What this pins is the part that would break
 * silently: that Save sends the three fields the PATCH route enforces, and
 * that it sends NOTHING else — the route re-derives an agent's focus whenever
 * a title or description arrives, so a budget edit that carried prose along
 * would quietly rewrite the tools the agent reaches for.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AgentSpecSheet } from "@/components/society/card/AgentSpecSheet";
import type { SocietyAgent } from "@/components/society/data";

const AGENT: SocietyAgent = {
  agentId: "gamil-agent",
  name: "Gamil agent",
  title: "Specialist",
  description: "Look after the mail.",
  tier: "specialist",
  provider: "",
  providerLabel: "",
  model: "",
  effort: "",
  figure: null,
  palette: { primary: "#2f6f4f", secondary: "#8a5a3b", accent: "#ffd166" },
  grantMode: "all",
  toolGrants: [],
  focus: ["plugin:gmail"],
  denies: [],
  approvalRules: { requireApproval: [], alwaysAllow: [] },
  permissionCeiling: "monitor",
  dailyBudgetUsd: 2,
  checkpoint: "idle",
  state: "idle",
  lifecycle: "active",
  createdMs: 1_788_000_000_000,
  maxConcurrentRuns: 1,
  workspaceDir: "society/gamil-agent/workspace",
  wikiNamespace: "society/gamil-agent/",
  chatSessionId: "society:gamil-agent",
  routines: [],
  stats: { runs: 0, totalCostUsd: 0, spentTodayUsd: 0, lastActiveMs: null },
};

function renderCard(over: Partial<SocietyAgent> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AgentSpecSheet agent={{ ...AGENT, ...over }} />
    </QueryClientProvider>,
  );
}

let fetchMock: ReturnType<typeof vi.fn>;

describe("the card's limits", () => {
  beforeEach(() => {
    fetchMock = vi.fn(async (url: string) => {
      if (String(url).includes("/capabilities")) {
        return { ok: true, json: async () => ({ capabilities: [] }) } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  test("shows the cap beside today's spend, so the budget reads as the gate it is", async () => {
    renderCard();
    await waitFor(() => expect(screen.getByTestId("agent-limits-edit")).toBeTruthy());
    expect(screen.getByTestId("agent-card-sheet").textContent).toContain("$0.00 of $2.00");
  });

  test("saves the three enforced knobs, and nothing that would re-derive the tools", async () => {
    renderCard();
    fireEvent.click(await screen.findByTestId("agent-limits-edit"));

    fireEvent.change(screen.getByTestId("agent-budget-input"), { target: { value: "7.5" } });
    fireEvent.click(screen.getByText("Ask first"));
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find((c) => c[1]?.method === "PATCH");
      expect(patch).toBeTruthy();
      const body = JSON.parse(String(patch?.[1]?.body));
      expect(body).toEqual({
        daily_budget_usd: 7.5,
        permission_ceiling: "ask",
        max_concurrent_runs: 1,
      });
    });
  });

  test("an empty budget means no cap rather than a broken number", async () => {
    renderCard();
    fireEvent.click(await screen.findByTestId("agent-limits-edit"));
    fireEvent.change(screen.getByTestId("agent-budget-input"), { target: { value: "" } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find((c) => c[1]?.method === "PATCH");
      expect(JSON.parse(String(patch?.[1]?.body)).daily_budget_usd).toBe(0);
    });
  });

  test("switching the cap off sends 0, which is the scheduler's no-cap", async () => {
    renderCard();
    fireEvent.click(await screen.findByTestId("agent-limits-edit"));
    fireEvent.click(screen.getByTestId("agent-budget-switch"));
    expect(screen.queryByTestId("agent-budget-input")).toBeNull();
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find((c) => c[1]?.method === "PATCH");
      expect(JSON.parse(String(patch?.[1]?.body)).daily_budget_usd).toBe(0);
    });
  });

  test("a stored 0 reads as the cap switched off, and turning it on restores a number", async () => {
    renderCard({ dailyBudgetUsd: 0 });
    await waitFor(() => expect(screen.getByTestId("agent-limits-edit")).toBeTruthy());
    expect(screen.getByTestId("agent-card-sheet").textContent).toContain("no cap");

    fireEvent.click(screen.getByTestId("agent-limits-edit"));
    expect(screen.getByTestId("agent-budget-switch").getAttribute("aria-checked")).toBe("false");
    expect(screen.queryByTestId("agent-budget-input")).toBeNull();

    fireEvent.click(screen.getByTestId("agent-budget-switch"));
    expect(screen.getByTestId("agent-budget-input")).toBeTruthy();
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find((c) => c[1]?.method === "PATCH");
      expect(JSON.parse(String(patch?.[1]?.body)).daily_budget_usd).toBe(2);
    });
  });
});
