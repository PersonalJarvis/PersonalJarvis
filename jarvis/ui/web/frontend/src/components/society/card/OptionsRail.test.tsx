/**
 * The chat-face Options rail: preview + this agent's routines, Retire behind
 * ⋯ (still two-press), and a switch of agent swaps both panes.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { OptionsRail } from "@/components/society/card/OptionsRail";
import { AgentChatLayout } from "./AgentChatLayout";
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

function mount(row: SocietyAgent, onRetired = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity, refetchInterval: false } },
  });
  return {
    onRetired,
    client,
    ...render(
      <QueryClientProvider client={client}>
        <AgentChatLayout options={<OptionsRail agent={row} onRetired={onRetired} />}>
          <section aria-label="Chat"><textarea aria-label="Chat draft" /></section>
        </AgentChatLayout>
      </QueryClientProvider>,
    ),
  };
}

function json(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

describe("OptionsRail", () => {
  beforeEach(() => {
    fetchMock = vi.fn(async (url: string) => {
      const path = String(url);
      if (path === "/api/tasks/gm1") {
        return json({ id: "gm1", title: "Inbox sweep", state: "scheduled", steps: [],
          spec: { action: { kind: "agent", prompt: "Read the inbox." } } });
      }
      if (path.includes("/browser/status")) {
        return json({ installed: false, phase: "idle", percent: 0, running: false });
      }
      if (path.includes("/gamil-agent/browser")) {
        return json({ installed: false, mode: "own", logged_in_profile: false, running: false });
      }
      if (path.includes("/scout/browser")) {
        return json({ installed: false, mode: "own", logged_in_profile: false, running: false });
      }
      if (path.includes("/gamil-agent/routines")) {
        return json({
          routines: [
            {
              id: "gm1",
              title: "[agent:Gamil] Inbox sweep",
              state: "scheduled",
              trigger: { kind: "every", interval_seconds: 18_000 },
            },
          ],
        });
      }
      if (path.includes("/scout/routines")) {
        return json({
          routines: [
            {
              id: "sc1",
              title: "[agent:Scout] Dawn brief",
              state: "scheduled",
              trigger: { kind: "every", interval_seconds: 86_400 },
            },
          ],
        });
      }
      if (path.includes("/api/society/agents")) return json({ agents: [] });
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  test("shows the screen and this agent's routines, not a full-width Retire", async () => {
    mount(agent());
    await waitFor(() => expect(screen.getByTestId("agent-browser-preview")).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId("agent-routines").textContent).toContain("Inbox sweep"));
    expect(screen.queryByTestId("agent-card-retire")).toBeNull();
    expect(screen.getByTestId("agent-card-options-more")).toBeTruthy();
  });

  test("Retire lives behind ⋯ and still only arms on the first press", async () => {
    const { onRetired } = mount(agent());
    fireEvent.click(screen.getByTestId("agent-card-options-more"));
    const retire = await screen.findByTestId("agent-card-retire");
    fireEvent.click(retire);
    expect(retire.dataset.armed).toBe("true");
    expect(onRetired).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "DELETE")).toHaveLength(0);
  });

  test("collapses the entire rail with a reachable toggle and preserves browser and routine drafts", async () => {
    mount(agent());
    await screen.findByText("Inbox sweep");
    const toggle = await screen.findByRole("button", { name: "Collapse Options" });
    const panel = document.getElementById(toggle.getAttribute("aria-controls")!)!;
    const browser = screen.getByTestId("agent-browser-preview");
    const canvas = browser.querySelector("canvas");
    fireEvent.click(screen.getByTestId("agent-routines-add"));
    const title = screen.getByRole("textbox", { name: "Title" }) as HTMLInputElement;
    fireEvent.change(title, { target: { value: "Unsaved routine" } });
    const chat = screen.getByRole("textbox", { name: "Chat draft" }) as HTMLTextAreaElement;
    fireEvent.change(chat, { target: { value: "Unsent message" } });

    toggle.focus();
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(panel.hidden).toBe(true);
    expect(screen.queryByRole("complementary", { name: "Options" })).toBeNull();
    expect(document.activeElement).toBe(toggle);
    expect(screen.getByRole("textbox", { name: "Chat draft" })).toBe(chat);
    expect(chat.value).toBe("Unsent message");

    fireEvent.click(screen.getByRole("button", { name: "Expand Options" }));
    expect(panel.hidden).toBe(false);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("agent-browser-preview")).toBe(browser);
    expect(browser.querySelector("canvas")).toBe(canvas);
    expect(screen.getByRole("textbox", { name: "Title" })).toBe(title);
    expect(title.value).toBe("Unsaved routine");
    expect(fetchMock.mock.calls.some(([, init]) => {
      const method = (init as RequestInit | undefined)?.method ?? "GET";
      return method !== "GET";
    })).toBe(false);
  });

  test("keeps the selected routine detail open when Options is folded", async () => {
    mount(agent());
    fireEvent.click(await screen.findByText("Inbox sweep"));
    const detail = await screen.findByTestId("agent-routine-detail");
    const toggle = await screen.findByRole("button", { name: "Collapse Options" });
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Expand Options" }));
    expect(screen.getByTestId("agent-routine-detail")).toBe(detail);
  });

  test("switching agent swaps the caption and the routines list", async () => {
    const { rerender, client } = mount(agent());
    await waitFor(() => expect(screen.getByTestId("agent-routines").textContent).toContain("Inbox sweep"));

    rerender(
      <QueryClientProvider client={client}>
        <OptionsRail agent={agent({ agentId: "scout", name: "Scout" })} onRetired={() => undefined} />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("agent-routines").textContent).toContain("Dawn brief"));
    expect(screen.getByTestId("agent-browser-preview").textContent).toContain("Scout");
    expect(screen.getByTestId("agent-routines").textContent).not.toContain("Inbox sweep");
  });
});
