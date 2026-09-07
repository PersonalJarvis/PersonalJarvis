/**
 * The preview is a placeholder, not a fake Chrome. These pin the honest
 * states and that Set up hits the existing install route.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AgentBrowserPreview } from "@/components/society/card/AgentBrowserPreview";
import type { SocietyAgent } from "@/components/society/data";

function agent(over: Partial<SocietyAgent> = {}): SocietyAgent {
  return {
    agentId: "gamil-agent",
    name: "Gamil agent",
    title: "Specialist",
    description: "",
    tier: "specialist",
    provider: "",
    providerLabel: "",
    model: "",
    effort: "",
    figure: null,
    palette: { primary: "#2f6f4f", secondary: "#8a5a3b", accent: "#ffd166" },
    grantMode: "all",
    toolGrants: [],
    focus: [],
    denies: [],
    approvalRules: { requireApproval: [], alwaysAllow: [] },
    permissionCeiling: "safe",
    dailyBudgetUsd: 0,
    checkpoint: "idle",
    state: "idle",
    lifecycle: "active",
    createdMs: 0,
    chatSessionId: null,
    routines: [],
    stats: { runs: 0, totalCostUsd: 0, spentTodayUsd: 0, lastActiveMs: null },
    ...over,
  } as SocietyAgent;
}

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity, refetchInterval: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AgentBrowserPreview agent={agent()} />
    </QueryClientProvider>,
  );
}

function json(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

describe("AgentBrowserPreview", () => {
  beforeEach(() => {
    fetchMock = vi.fn(async (url: string) => {
      const path = String(url);
      if (path.includes("/browser/status")) {
        return json({ installed: false, phase: "idle", percent: 0, detail: "", error: "", running: false });
      }
      if (path.includes("/browser/install")) {
        return json({ started: true, installed: false, phase: "preflight", percent: 0 });
      }
      if (path.includes("/browser")) {
        return json({ installed: false, mode: "own", logged_in_profile: false, running: false });
      }
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  test("names the agent and says the browser is not set up", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("agent-browser-preview").textContent).toContain("Gamil agent"));
    expect(screen.getByTestId("agent-browser-preview").textContent).toMatch(/not set up/i);
    expect(screen.getByTestId("agent-browser-setup")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    expect(document.querySelector("video")).toBeNull();
  });

  test("Set up posts the existing install route", async () => {
    mount();
    fireEvent.click(await screen.findByTestId("agent-browser-setup"));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          (c) => String(c[0]).includes("/api/society/browser/install") && (c[1] as RequestInit | undefined)?.method === "POST",
        ),
      ).toBe(true);
    });
  });
});
