/**
 * Component tests for the workspace's settings panel.
 *
 * What matters here is not the layout but WHICH PLAN gets spent: that the
 * toolbar names the account the next terminal opens on, that it stays silent
 * for the many people holding a single login, and that a switch made in here
 * travels through the workspace — the store route alone would leave the open
 * session believing the old account until the next full reload.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const pushToast = vi.fn();
vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ pushToast }),
}));

vi.mock("@/lib/agenticIdeApi", () => ({ setIdeActiveAccount: vi.fn() }));

vi.mock("@/lib/agentAccountsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/agentAccountsApi")>(
    "@/lib/agentAccountsApi",
  );
  return {
    ...actual,
    fetchAgentAccounts: vi.fn(),
    createAgentAccount: vi.fn(),
    setActiveAgentAccount: vi.fn(),
    loginAgentAccount: vi.fn(),
    deleteAgentAccount: vi.fn(),
    renameAgentAccount: vi.fn(),
  };
});

import { WorkspaceSettings } from "./WorkspaceSettings";
import { setIdeActiveAccount, type IdeAccountState } from "@/lib/agenticIdeApi";
import { fetchAgentAccounts, setActiveAgentAccount } from "@/lib/agentAccountsApi";

function ideAccounts(over: Partial<IdeAccountState> = {}): IdeAccountState[] {
  return [
    {
      agent: "claude",
      display_name: "Claude Code",
      active_account: "claude:default",
      active_label: "Default Claude Code login",
      account_count: 1,
      ...over,
    },
    {
      agent: "codex",
      display_name: "Codex",
      active_account: "codex:default",
      active_label: "Default Codex login",
      account_count: 1,
    },
  ];
}

function account(over: Record<string, unknown> = {}) {
  return {
    id: "claude:default",
    platform: "claude" as const,
    label: "Default Claude Code login",
    config_dir: "/home/u/.claude",
    builtin: true,
    connected: true,
    mode: "subscription",
    message: "Signed in via Claude Max.",
    email: null,
    tier: "max",
    ...over,
  };
}

const secondSeat = account({
  id: "claude:abc123",
  label: "Second seat",
  builtin: false,
  message: "Signed in via Claude Max.",
});

function accountsResponse() {
  return {
    platforms: [
      {
        platform: "claude" as const,
        active_account: "claude:default",
        accounts: [account(), secondSeat],
      },
      {
        platform: "codex" as const,
        active_account: "codex:default",
        accounts: [
          account({
            id: "codex:default",
            platform: "codex",
            label: "Default Codex login",
          }),
        ],
      },
    ],
  };
}

afterEach(cleanup);
beforeEach(() => {
  pushToast.mockReset();
  vi.mocked(setIdeActiveAccount).mockReset();
  vi.mocked(fetchAgentAccounts).mockReset();
  vi.mocked(setActiveAgentAccount).mockReset();
});

describe("the workspace's account chip", () => {
  it("stays away while there is only one login to choose from", () => {
    render(<WorkspaceSettings accounts={ideAccounts()} />);
    // The gear is always reachable — it is also where a second one is added.
    expect(screen.getByTestId("agentic-settings-open")).toBeTruthy();
    expect(screen.queryByTestId("active-account-claude")).toBeNull();
  });

  it("names the plan the next terminal will spend, once there are two", () => {
    render(
      <WorkspaceSettings
        accounts={ideAccounts({ active_label: "Second seat", account_count: 2 })}
      />,
    );
    expect(screen.getByTestId("active-account-claude").textContent).toContain(
      "Second seat",
    );
    // Codex still has one login, so it says nothing.
    expect(screen.queryByTestId("active-account-codex")).toBeNull();
  });

  it("opens the switcher when the chip itself is clicked", async () => {
    // The chip reads as a button, so it must behave as one — its first life as
    // a passive label made the switch look broken (2026-07-31 report): the one
    // clickable pixel was the gear beside it.
    vi.mocked(fetchAgentAccounts).mockResolvedValue(accountsResponse() as never);
    render(
      <WorkspaceSettings
        accounts={ideAccounts({ active_label: "Second seat", account_count: 2 })}
      />,
    );
    fireEvent.click(screen.getByTestId("active-account-claude"));
    await waitFor(() => expect(screen.getByTestId("agentic-settings")).toBeTruthy());
  });
});

describe("switching the account from inside the workspace", () => {
  it("shows which subscription each CLI is on right now", async () => {
    vi.mocked(fetchAgentAccounts).mockResolvedValue(accountsResponse() as never);
    render(
      <WorkspaceSettings
        accounts={ideAccounts({ active_label: "Second seat", account_count: 2 })}
      />,
    );
    fireEvent.click(screen.getByTestId("agentic-settings-open"));

    await waitFor(() =>
      expect(screen.getByTestId("settings-active-claude").textContent).toBe(
        "Second seat",
      ),
    );
    expect(screen.getByTestId("settings-active-codex").textContent).toBe(
      "Default Codex login",
    );
  });

  it("applies the switch through the workspace and hands the new state back", async () => {
    const switched = {
      active: true,
      session: null,
      max_terminals: 12,
      workspaces: [],
      active_id: null,
      max_workspaces: 6,
      accounts: ideAccounts({
        active_account: "claude:abc123",
        active_label: "Second seat",
        account_count: 2,
      }),
    };
    vi.mocked(fetchAgentAccounts).mockResolvedValue(accountsResponse() as never);
    vi.mocked(setIdeActiveAccount).mockResolvedValue(switched as never);
    const onStateChanged = vi.fn();

    render(
      <WorkspaceSettings
        accounts={ideAccounts({ account_count: 2 })}
        onStateChanged={onStateChanged}
      />,
    );
    fireEvent.click(screen.getByTestId("agentic-settings-open"));
    await waitFor(() => expect(screen.getByText("Second seat")).toBeTruthy());

    const row = screen.getByText("Second seat").closest("li")!;
    fireEvent.click(row.querySelector("button")!);

    await waitFor(() =>
      expect(setIdeActiveAccount).toHaveBeenCalledWith("claude", "claude:abc123"),
    );
    // Straight to the workspace — going through the account store alone would
    // leave the open session spawning panes on the previous plan.
    expect(setActiveAgentAccount).not.toHaveBeenCalled();
    await waitFor(() => expect(onStateChanged).toHaveBeenCalledWith(switched));
    expect(pushToast).toHaveBeenCalledWith(
      "success",
      "New Claude Code terminals will use Second seat.",
    );
  });

  it("says so instead of silently keeping the old account when the switch fails", async () => {
    vi.mocked(fetchAgentAccounts).mockResolvedValue(accountsResponse() as never);
    vi.mocked(setIdeActiveAccount).mockRejectedValue(
      new Error("That account no longer exists."),
    );

    render(<WorkspaceSettings accounts={ideAccounts({ account_count: 2 })} />);
    fireEvent.click(screen.getByTestId("agentic-settings-open"));
    await waitFor(() => expect(screen.getByText("Second seat")).toBeTruthy());
    fireEvent.click(screen.getByText("Second seat").closest("li")!.querySelector("button")!);

    await waitFor(() =>
      expect(pushToast).toHaveBeenCalledWith("error", "That account no longer exists."),
    );
  });
});
