/**
 * Component tests for the subscription switcher.
 *
 * The interesting cases are the ones where a wrong pixel means a wrong plan:
 * which row reads as the one new terminals will use, that a registered account
 * with no login is not dressed up as ready, and that the CLI's own default
 * login can be neither renamed nor removed from here.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AgentAccountsPanel } from "@/components/AgentAccountsPanel";
import {
  createAgentAccount,
  fetchAgentAccounts,
  loginAgentAccount,
  setActiveAgentAccount,
} from "@/lib/agentAccountsApi";

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

function account(over: Partial<Record<string, unknown>> = {}) {
  return {
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
    ...over,
  };
}

function response(claudeAccounts: unknown[], active = "claude:default") {
  return {
    platforms: [
      { platform: "claude" as const, active_account: active, accounts: claudeAccounts },
      {
        platform: "codex" as const,
        active_account: "codex:default",
        accounts: [
          account({
            id: "codex:default",
            platform: "codex",
            label: "Default Codex login",
            message: "Signed in via ChatGPT.",
          }),
        ],
      },
    ],
  };
}

const secondSeat = account({
  id: "claude:abc123",
  label: "Second seat",
  builtin: false,
  connected: true,
  message: "Signed in via Claude Max (two@example.com).",
  email: "two@example.com",
});

afterEach(cleanup);
beforeEach(() => {
  vi.mocked(fetchAgentAccounts).mockReset();
  vi.mocked(setActiveAgentAccount).mockReset();
  vi.mocked(createAgentAccount).mockReset();
  vi.mocked(loginAgentAccount).mockReset();
});

describe("AgentAccountsPanel", () => {
  it("lists every registered subscription of both CLIs", async () => {
    vi.mocked(fetchAgentAccounts).mockResolvedValue(
      response([account(), secondSeat]) as never,
    );
    render(<AgentAccountsPanel />);
    await waitFor(() => {
      expect(screen.getByText("Second seat")).toBeTruthy();
    });
    expect(screen.getByText("Default Claude Code login")).toBeTruthy();
    expect(screen.getByText("Default Codex login")).toBeTruthy();
  });

  it("marks exactly the account new terminals will use", async () => {
    vi.mocked(fetchAgentAccounts).mockResolvedValue(
      response([account(), secondSeat], "claude:abc123") as never,
    );
    render(<AgentAccountsPanel />);
    // One "in use" chip per platform — never two on one CLI, which would leave
    // the user unable to tell which plan the next terminal spends.
    await waitFor(() => {
      expect(screen.getAllByText("in use").length).toBe(2);
    });
    const active = screen
      .getByText("Second seat")
      .closest("li");
    expect(active?.textContent).toContain("in use");
  });

  it("switches the active account on click", async () => {
    vi.mocked(fetchAgentAccounts).mockResolvedValue(
      response([account(), secondSeat]) as never,
    );
    vi.mocked(setActiveAgentAccount).mockResolvedValue(
      response([account(), secondSeat], "claude:abc123") as never,
    );
    render(<AgentAccountsPanel />);
    await waitFor(() => expect(screen.getByText("Second seat")).toBeTruthy());

    const row = screen.getByText("Second seat").closest("li")!;
    fireEvent.click(row.querySelector("button")!);

    await waitFor(() => {
      expect(setActiveAgentAccount).toHaveBeenCalledWith("claude", "claude:abc123");
    });
  });

  it("shows a registered-but-unsigned account honestly, with a way in", async () => {
    const fresh = account({
      id: "claude:new1",
      label: "Fresh seat",
      builtin: false,
      connected: false,
      mode: "unknown",
      message: "Not signed in yet — use Sign in, and finish the flow in the window that opens.",
      email: null,
      tier: null,
    });
    vi.mocked(fetchAgentAccounts).mockResolvedValue(
      response([account(), fresh]) as never,
    );
    vi.mocked(loginAgentAccount).mockResolvedValue({ message: "Sign-in started" } as never);
    render(<AgentAccountsPanel />);
    await waitFor(() => expect(screen.getByText("Fresh seat")).toBeTruthy());

    const row = screen.getByText("Fresh seat").closest("li")!;
    expect(row.textContent).toContain("Not signed in yet");
    fireEvent.click(screen.getByText("Sign in"));
    await waitFor(() => {
      expect(loginAgentAccount).toHaveBeenCalledWith("claude:new1");
    });
  });

  it("offers no rename or remove on the CLI's own default login", async () => {
    vi.mocked(fetchAgentAccounts).mockResolvedValue(response([account()]) as never);
    render(<AgentAccountsPanel />);
    await waitFor(() =>
      expect(screen.getByText("Default Claude Code login")).toBeTruthy(),
    );
    const row = screen.getByText("Default Claude Code login").closest("li")!;
    expect(row.querySelector('[aria-label="Rename"]')).toBeNull();
    expect(row.querySelector('[aria-label="Remove"]')).toBeNull();
  });

  it("adds a subscription with the name the user typed", async () => {
    vi.mocked(fetchAgentAccounts).mockResolvedValue(response([account()]) as never);
    vi.mocked(createAgentAccount).mockResolvedValue(
      response([account(), secondSeat]) as never,
    );
    render(<AgentAccountsPanel />);
    await waitFor(() =>
      expect(screen.getByText("Default Claude Code login")).toBeTruthy(),
    );

    fireEvent.click(screen.getAllByText("Add account")[0]);
    fireEvent.change(screen.getByPlaceholderText(/Name, e\.g\./i), {
      target: { value: "Second seat" },
    });
    fireEvent.click(screen.getByText("Add"));

    await waitFor(() => {
      expect(createAgentAccount).toHaveBeenCalledWith("claude", "Second seat");
    });
  });

  it("shows the duplicate-subscription warning on the row that collides", async () => {
    // 2026-07-27: a browser with a live claude.com session approved the second
    // sign-in against the FIRST account without ever showing a code, so two
    // rows named one plan. Both looked perfectly healthy; the only symptom was
    // usage draining twice as fast. The backend flags it — the row must show it.
    const twin = account({
      id: "claude:abc123",
      label: "Second seat",
      builtin: false,
      warning:
        "This is the same subscription as 'Default Claude Code login' — both are " +
        "signed in as the same account, so they share one plan's usage.",
    });
    vi.mocked(fetchAgentAccounts).mockResolvedValue(
      response([account(), twin]) as never,
    );
    render(<AgentAccountsPanel />);
    await waitFor(() => expect(screen.getByText("Second seat")).toBeTruthy());

    const row = screen.getByText("Second seat").closest("li")!;
    expect(row.textContent).toContain("same subscription");
    // and the account it duplicates stays clean, or every row cries wolf
    const original = screen.getByText("Default Claude Code login").closest("li")!;
    expect(original.textContent).not.toContain("same subscription");
  });

  it("reports a failed load instead of rendering an empty, confident list", async () => {
    vi.mocked(fetchAgentAccounts).mockRejectedValue(new Error("backend is warming up"));
    render(<AgentAccountsPanel />);
    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toContain("backend is warming up");
    });
  });
});
