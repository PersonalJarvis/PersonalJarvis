import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { fetchAgentAccounts, fetchAgentUsage, type AgentAccount } from "@/lib/agentAccountsApi";
import { useEventStore } from "@/store/events";
import { SubscriptionsTab } from "./SubscriptionsTab";
import { groupSubscriptions, resetText, windowLength } from "./subscriptionsModel";

vi.mock("@/lib/agentResetsApi", () => ({ fetchAccountResets: vi.fn(async () => ({
  status: "unsupported", credits: null, available_count: null, can_redeem: false, usage: null,
})), consumeAccountReset: vi.fn(), ResetRequestError: class extends Error {} }));

vi.mock("@/lib/agentAccountsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/agentAccountsApi")>("@/lib/agentAccountsApi");
  return { ...actual, fetchAgentAccounts: vi.fn(), fetchAgentUsage: vi.fn() };
});

function account(id: string, platform: string, extra: Partial<AgentAccount> = {}): AgentAccount {
  return {
    id,
    platform,
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
      active_account: "c2",
      accounts: [account("c1", "claude"), account("c2", "claude"), account("c3", "claude", { connected: false })],
    },
    {
      platform: "codex",
      display_name: "Codex",
      active_account: "x1",
      accounts: [account("x1", "codex", { tier: "Pro" })],
    },
    {
      platform: "gemini",
      display_name: "Gemini CLI",
      active_account: "g1",
      accounts: [account("g1", "gemini", { connected: false })],
    },
  ],
};

const USAGE = {
  accounts: [
    {
      account_id: "c1",
      platform: "claude",
      status: "ok",
      windows: [
        {
          kind: "session",
          percent: 42,
          severity: "normal",
          resets_at: new Date(Date.now() + 90 * 60_000).toISOString(),
          window_minutes: 300,
          scope_label: null,
          raw_label: null,
        },
        {
          kind: "weekly",
          percent: 81,
          severity: "warning",
          resets_at: null,
          window_minutes: null,
          scope_label: null,
          raw_label: null,
        },
      ],
      source: "live",
      as_of: null,
      message: "",
      plan: "Max 20x",
    },
    {
      account_id: "x1",
      platform: "codex",
      status: "unsupported",
      windows: [],
      source: "live",
      as_of: null,
      message: "",
      plan: null,
    },
  ],
  ttl_seconds: 60,
  generated_at: 0,
};

beforeEach(() => {
  useEventStore.setState({ activeSection: "agentic-ide" });
  vi.mocked(fetchAgentAccounts).mockResolvedValue(ACCOUNTS);
  vi.mocked(fetchAgentUsage).mockResolvedValue(USAGE);
});

afterEach(() => {
  cleanup();
  vi.mocked(fetchAgentAccounts).mockReset();
  vi.mocked(fetchAgentUsage).mockReset();
});

describe("SubscriptionsTab", () => {
  it("groups signed-in subscriptions into one block per tool", async () => {
    render(<SubscriptionsTab />);
    const groups = await screen.findAllByTestId("subscription-group");
    expect(groups.map((group) => group.dataset.platform)).toEqual(["claude", "codex"]);
    const claudeRows = within(groups[0]).getAllByTestId("subscription-row");
    expect(claudeRows.map((row) => row.dataset.account)).toEqual(["c1", "c2"]);
    expect(claudeRows[1].dataset.active).toBe("true");
    expect(within(groups[0]).getByText("c1@example.com")).toBeTruthy();
    expect(within(groups[0]).getByText("Seat c1 · Max 20x")).toBeTruthy();
  });

  it("names every seat on a subscription row shared by two seats", async () => {
    const shared = {
      platforms: [
        {
          ...ACCOUNTS.platforms[0],
          accounts: [account("c1", "claude"), account("c2", "claude", { email: "c1@example.com" })],
        },
      ],
    };
    vi.mocked(fetchAgentAccounts).mockResolvedValue(shared);
    render(<SubscriptionsTab />);
    const [group] = await screen.findAllByTestId("subscription-group");
    const rows = within(group).getAllByTestId("subscription-row");
    expect(rows.map((row) => row.dataset.account)).toEqual(["c2"]);
    expect(within(group).getByText("Seat c1, Seat c2 · Max 20x")).toBeTruthy();
  });

  it("shows plan usage only after a row is expanded", async () => {
    render(<SubscriptionsTab />);
    const [row] = await screen.findAllByTestId("subscription-row");
    await screen.findByTestId("subscription-peak");
    expect(within(row).queryByTestId("subscription-details")).toBeNull();
    const toggle = within(row).getByRole("button");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const bars = within(row).getAllByRole("progressbar");
    expect(bars.map((bar) => bar.getAttribute("aria-valuenow"))).toEqual(["42", "81"]);
    expect(within(row).getByText("Current session (5 h)")).toBeTruthy();
    expect(within(row).getByText("Resets in 1 h 30 min")).toBeTruthy();
  });

  it("says so when a tool reports no usage", async () => {
    render(<SubscriptionsTab />);
    const groups = await screen.findAllByTestId("subscription-group");
    await screen.findByTestId("subscription-peak");
    fireEvent.click(within(groups[1]).getByRole("button"));
    expect(within(groups[1]).getByTestId("subscription-usage-status").textContent).toBe(
      "This tool does not report plan usage.",
    );
  });

  it("shows an empty state when nothing is signed in", async () => {
    vi.mocked(fetchAgentAccounts).mockResolvedValue({ platforms: [ACCOUNTS.platforms[2]] });
    render(<SubscriptionsTab />);
    expect(await screen.findByTestId("subscriptions-empty")).toBeTruthy();
  });
});

describe("subscriptionsModel", () => {
  it("drops tools without a signed-in subscription", () => {
    expect(groupSubscriptions(ACCOUNTS, []).map((group) => group.platform)).toEqual(["claude", "codex"]);
    expect(groupSubscriptions(null, [])).toEqual([]);
  });

  it("shows seats signed in as the same email as one subscription", () => {
    const accounts = {
      platforms: [
        {
          platform: "claude",
          display_name: "Claude Code",
          active_account: "c3",
          accounts: [
            account("c1", "claude", { email: "Me@example.com" }),
            account("c2", "claude"),
            account("c3", "claude", { email: "me@example.com", warning: "same subscription" }),
            account("c4", "claude", { email: null }),
            account("c5", "claude", { email: null }),
          ],
        },
      ],
    };
    const usage = [{ ...USAGE.accounts[0], account_id: "c3" }];
    const [group] = groupSubscriptions(accounts, usage);
    expect(group.rows.map((row) => row.account.id)).toEqual(["c3", "c2", "c4", "c5"]);
    expect(group.rows[0].seats.map((seat) => seat.id)).toEqual(["c1", "c3"]);
    expect(group.rows[0].active).toBe(true);
    expect(group.rows[0].usage?.account_id).toBe("c3");
    expect(group.rows[0].account.warning).toBe("same subscription");
  });

  it("words reset times as a countdown inside a day", () => {
    const now = Date.parse("2026-10-05T12:00:00Z");
    expect(resetText(null, now, "en")).toBeNull();
    expect(resetText("garbage", now, "en")).toBeNull();
    expect(resetText("2026-10-05T12:00:20Z", now, "en")).toEqual({ key: "reset_soon" });
    expect(resetText("2026-10-05T12:45:00Z", now, "en")).toEqual({ key: "reset_in_minutes", vars: { m: 45 } });
    expect(resetText("2026-10-05T15:10:00Z", now, "en")).toEqual({ key: "reset_in_hours", vars: { h: 3, m: 10 } });
    expect(resetText("2026-10-08T12:00:00Z", now, "en")?.key).toBe("reset_on");
  });

  it("writes window lengths compactly", () => {
    expect(windowLength(300)).toBe("5 h");
    expect(windowLength(10080)).toBe("7 d");
    expect(windowLength(30)).toBe("30 min");
    expect(windowLength(null)).toBe("");
  });
});
