import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import {
  fetchAgentAccounts,
  fetchAgentUsage,
  fetchAutoSwitch,
  switchSeat,
  updateAutoSwitch,
  type AgentAccount,
  type AutoSwitchState,
} from "@/lib/agentAccountsApi";
import { useEventStore } from "@/store/events";
import { SubscriptionsTab } from "./SubscriptionsTab";

vi.mock("@/lib/agentAccountsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/agentAccountsApi")>("@/lib/agentAccountsApi");
  return {
    ...actual,
    fetchAgentAccounts: vi.fn(),
    fetchAgentUsage: vi.fn(),
    fetchAutoSwitch: vi.fn(),
    switchSeat: vi.fn(),
    updateAutoSwitch: vi.fn(),
  };
});

function account(id: string, extra: Partial<AgentAccount> = {}): AgentAccount {
  return {
    id,
    platform: "claude",
    label: `Seat ${id}`,
    config_dir: `/home/u/${id}`,
    builtin: false,
    connected: true,
    mode: "subscription",
    message: "",
    email: `${id}@example.com`,
    tier: null,
    ...extra,
  };
}

const ACCOUNTS = {
  platforms: [
    {
      platform: "claude",
      display_name: "Claude Code",
      active_account: "c1",
      accounts: [account("c1"), account("c2"), account("c3")],
    },
  ],
};

const STATE: AutoSwitchState = {
  enabled: true,
  at_percent: 97,
  watching: true,
  exhausted: [{ account_id: "c3", until: Date.now() / 1000 + 3600 }],
  events: [
    {
      at: Date.now() / 1000 - 60,
      platform: "claude",
      reason: "limit",
      to_account: "c1",
      to_label: "Seat c1",
      from_label: "Seat c3",
      moved: 2,
      queued: 0,
    },
  ],
};

beforeEach(() => {
  useEventStore.setState({ activeSection: "agentic-ide" });
  vi.mocked(fetchAgentAccounts).mockResolvedValue(ACCOUNTS);
  vi.mocked(fetchAgentUsage).mockResolvedValue({ accounts: [], ttl_seconds: 60, generated_at: 0 });
  vi.mocked(fetchAutoSwitch).mockResolvedValue(STATE);
});

afterEach(() => {
  cleanup();
  vi.mocked(fetchAgentAccounts).mockReset();
  vi.mocked(fetchAgentUsage).mockReset();
  vi.mocked(fetchAutoSwitch).mockReset();
  vi.mocked(switchSeat).mockReset();
  vi.mocked(updateAutoSwitch).mockReset();
});

async function rows() {
  render(<SubscriptionsTab />);
  await screen.findByTestId("auto-switch-bar");
  return screen.findAllByTestId("subscription-row");
}

describe("seat switching in the Subscriptions tab", () => {
  it("offers a one-click switch on every row except the active one", async () => {
    const [active, second, third] = await rows();
    expect(within(active).queryByTestId("seat-switch")).toBeNull();
    expect(within(second).getByTestId("seat-switch")).toBeTruthy();
    expect(within(third).getByTestId("seat-switch")).toBeTruthy();
  });

  it("moves the tool and its running agents with one click and says what happened", async () => {
    vi.mocked(switchSeat).mockResolvedValue({
      active_account: "c2",
      active_label: "Seat c2",
      moved: 2,
      queued: 1,
      message: "",
    });
    const [, second] = await rows();
    fireEvent.click(within(second).getByTestId("seat-switch"));
    await waitFor(() => expect(switchSeat).toHaveBeenCalledWith("claude", "c2"));
    const notice = await screen.findByTestId("seat-switch-notice");
    expect(notice.textContent).toContain("Seat c2");
    expect(notice.textContent).toContain("2");
    expect(notice.textContent).toContain("1");
    // The account list is read again so the Active badge follows.
    await waitFor(() => expect(vi.mocked(fetchAgentAccounts).mock.calls.length).toBeGreaterThan(1));
  });

  it("reports a failed switch instead of pretending", async () => {
    vi.mocked(switchSeat).mockRejectedValue(new Error("That account no longer exists."));
    const [, second] = await rows();
    fireEvent.click(within(second).getByTestId("seat-switch"));
    const notice = await screen.findByTestId("seat-switch-notice");
    expect(notice.textContent).toContain("That account no longer exists.");
  });

  it("marks a seat that is used up and shows the last automatic switch", async () => {
    const [, , third] = await rows();
    expect(within(third).getByTestId("seat-empty")).toBeTruthy();
    const event = await screen.findByTestId("seat-switch-event");
    expect(event.textContent).toContain("Seat c3");
    expect(event.textContent).toContain("Seat c1");
  });

  it("turns automatic switching off", async () => {
    vi.mocked(updateAutoSwitch).mockResolvedValue({ ...STATE, enabled: false });
    await rows();
    fireEvent.click(screen.getByTestId("auto-switch-toggle"));
    await waitFor(() => expect(updateAutoSwitch).toHaveBeenCalledWith({ enabled: false }));
  });

  it("draws no switching UI on a backend without the feature", async () => {
    vi.mocked(fetchAutoSwitch).mockResolvedValue(null);
    render(<SubscriptionsTab />);
    await screen.findAllByTestId("subscription-row");
    expect(screen.queryByTestId("auto-switch-bar")).toBeNull();
    expect(screen.queryByTestId("seat-switch")).toBeNull();
  });
});
