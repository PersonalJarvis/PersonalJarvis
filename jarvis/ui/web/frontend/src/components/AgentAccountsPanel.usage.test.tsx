/**
 * The switcher's behaviour around plan usage, including the awkward middle state.
 *
 * The app's Python server does not pick up a new route while it is running, so
 * there is a real window — between updating and the next restart — where this
 * panel's new code talks to a backend that has never heard of usage and every
 * call 404s. That was observed live on 2026-08-13, and the first version made it
 * look broken: a Refresh button sat in the header doing nothing, forever, once a
 * minute. So "no usage route here" is treated as a capability answer, not an
 * error, and the whole block disappears instead.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AgentAccountsPanel } from "@/components/AgentAccountsPanel";
import { fetchAgentAccounts, fetchAgentUsage } from "@/lib/agentAccountsApi";

vi.mock("@/lib/agentAccountsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/agentAccountsApi")>(
    "@/lib/agentAccountsApi",
  );
  return {
    ...actual,
    fetchAgentAccounts: vi.fn(),
    fetchAgentUsage: vi.fn(),
  };
});

const ACCOUNTS = {
  platforms: [
    {
      platform: "claude" as const,
      display_name: "Claude Code",
      active_account: "claude:default",
      accounts: [
        {
          id: "claude:default",
          platform: "claude" as const,
          label: "Default Claude Code login",
          config_dir: "/home/u/.claude",
          builtin: true,
          connected: true,
          mode: "subscription",
          message: "Signed in via Claude Max (one@example.com).",
          email: "one@example.com",
          tier: "max",
        },
      ],
    },
  ],
};

const USAGE = {
  accounts: [
    {
      account_id: "claude:default",
      platform: "claude" as const,
      status: "ok",
      windows: [
        {
          kind: "session",
          percent: 12,
          severity: "normal",
          resets_at: null,
          window_minutes: 300,
          scope_label: null,
          raw_label: null,
        },
        {
          kind: "weekly",
          percent: 74,
          severity: "normal",
          resets_at: null,
          window_minutes: 10080,
          scope_label: null,
          raw_label: null,
        },
      ],
      source: "live",
      as_of: Date.now() / 1000,
      message: "",
      plan: "Max 20x",
    },
  ],
  ttl_seconds: 60,
  generated_at: Date.now() / 1000,
};

beforeEach(() => {
  vi.mocked(fetchAgentAccounts).mockResolvedValue(ACCOUNTS as never);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AgentAccountsPanel — plan usage", () => {
  it("draws the meters and offers a refresh once the backend answers", async () => {
    vi.mocked(fetchAgentUsage).mockResolvedValue(USAGE as never);
    render(<AgentAccountsPanel />);

    await waitFor(() => expect(screen.getAllByRole("progressbar")).toHaveLength(2));
    expect(screen.getByText("Max 20x")).toBeTruthy();

    const refresh = screen.getByRole("button", { name: /refresh/i });
    fireEvent.click(refresh);
    // The manual button must BYPASS the server cache, otherwise it hands back
    // the same minute-old number and reads as a button that does nothing.
    await waitFor(() => expect(fetchAgentUsage).toHaveBeenCalledWith(true));
  });

  it("hides the whole block on a backend that has no usage route", async () => {
    // What a 404 becomes after the api client translates it.
    vi.mocked(fetchAgentUsage).mockResolvedValue(null as never);
    render(<AgentAccountsPanel />);

    await waitFor(() => expect(screen.getByText(/Default Claude Code login/)).toBeTruthy());
    expect(screen.queryAllByRole("progressbar")).toHaveLength(0);
    // The button is the point: offering a control that cannot work is worse
    // than offering nothing, because it reads as a broken feature.
    expect(screen.queryByRole("button", { name: /refresh/i })).toBeNull();
  });

  it("keeps the account list working when the usage read fails outright", async () => {
    vi.mocked(fetchAgentUsage).mockRejectedValue(new Error("network is down"));
    render(<AgentAccountsPanel />);

    // Usage sits on top of the switcher. A failed read must never cost the
    // rows the user actually came here to operate.
    await waitFor(() => expect(screen.getByText(/Default Claude Code login/)).toBeTruthy());
    expect(screen.queryAllByRole("progressbar")).toHaveLength(0);
  });
});
