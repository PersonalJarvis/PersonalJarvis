/**
 * The safety properties of the one irreversible control on the card.
 *
 * Not the ceremony (that is `world/retirement.test.ts`) and not the styling —
 * only the three things that would let someone delete an agent they did not
 * mean to: a single press doing it, an armed button waiting around, and the
 * lead being offered at all.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { RetireButton } from "@/components/society/card/RetireButton";
import type { SocietyAgent } from "@/components/society/data";

function agent(over: Partial<SocietyAgent> = {}): SocietyAgent {
  return {
    agentId: "scout",
    name: "Scout",
    title: "Research coordinator",
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

function mount(node: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

const button = () => screen.getByTestId("agent-card-retire");

/** Every DELETE the component fired — the roster GET it mounts with is fine. */
function deletes(): unknown[] {
  const mock = fetch as unknown as { mock: { calls: [string, RequestInit?][] } };
  return mock.mock.calls.filter(([, init]) => init?.method === "DELETE");
}

describe("RetireButton", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // The roster query mounts with the component and is answered honestly;
    // a DELETE, on the other hand, would mean the arming logic let one
    // through, and `deletes()` below makes that loud rather than silent.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({ agents: [] }),
      })),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  test("one press only arms it — the agent is not retired yet", () => {
    const onRetired = vi.fn();
    mount(<RetireButton agent={agent()} onRetired={onRetired} />);

    expect(button().dataset.armed).toBeUndefined();
    fireEvent.click(button());
    expect(button().dataset.armed).toBe("true");
    expect(onRetired).not.toHaveBeenCalled();
    expect(deletes()).toHaveLength(0);
  });

  test("an armed button disarms itself, so a card left open is not a loaded gun", () => {
    mount(<RetireButton agent={agent()} />);
    fireEvent.click(button());
    expect(button().dataset.armed).toBe("true");

    act(() => {
      vi.advanceTimersByTime(6_100);
    });
    expect(button().dataset.armed).toBeUndefined();
  });

  test("the lead cannot be retired, and the button says so instead of failing at the API", () => {
    mount(<RetireButton agent={agent({ agentId: "jarvis", name: "Jarvis", tier: "lead" })} />);
    const el = button() as HTMLButtonElement;
    expect(el.disabled).toBe(true);

    fireEvent.click(el);
    expect(el.dataset.armed).toBeUndefined();
    expect(deletes()).toHaveLength(0);
  });

  test("switching to another agent in the rail drops the armed state", () => {
    const { rerender } = mount(<RetireButton agent={agent()} variant="rail" />);
    fireEvent.click(button());
    expect(button().dataset.armed).toBe("true");

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rerender(
      <QueryClientProvider client={client}>
        <RetireButton agent={agent({ agentId: "archivist", name: "Archivist" })} variant="rail" />
      </QueryClientProvider>,
    );
    expect(button().dataset.armed).toBeUndefined();
  });
});
