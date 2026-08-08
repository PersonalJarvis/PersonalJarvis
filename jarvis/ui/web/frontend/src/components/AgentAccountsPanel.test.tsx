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
  type LoginFlowState,
  createAgentAccount,
  fetchAgentAccounts,
  getLoginFlow,
  loginAgentAccount,
  setActiveAgentAccount,
  startLoginFlow,
  submitLoginFlowCode,
} from "@/lib/agentAccountsApi";
import { openExternalUrl } from "@/lib/openExternal";

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
    startLoginFlow: vi.fn(),
    getLoginFlow: vi.fn(),
    submitLoginFlowCode: vi.fn(),
    cancelLoginFlow: vi.fn(),
  };
});

vi.mock("@/lib/openExternal", () => ({
  openExternalUrl: vi.fn(async () => undefined),
}));

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

function flowState(over: Partial<LoginFlowState> = {}): LoginFlowState {
  return {
    flow_id: "flow-1",
    account_id: "claude:new1",
    platform: "claude",
    label: "Fresh seat",
    status: "awaiting_input",
    url: "https://claude.ai/oauth/authorize?client_id=abc&state=xyz",
    code_expected: true,
    message: "",
    tail: "Paste code here if prompted:",
    finished: false,
    ...over,
  };
}

afterEach(cleanup);
beforeEach(() => {
  vi.mocked(fetchAgentAccounts).mockReset();
  vi.mocked(setActiveAgentAccount).mockReset();
  vi.mocked(createAgentAccount).mockReset();
  vi.mocked(loginAgentAccount).mockReset();
  vi.mocked(startLoginFlow).mockReset();
  vi.mocked(getLoginFlow).mockReset();
  vi.mocked(submitLoginFlowCode).mockReset();
  vi.mocked(openExternalUrl).mockReset();
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

  it("signs in IN-APP: link to copy, code field, code handed to the flow", async () => {
    /*
     * The old path opened a raw console window, and that console is where the
     * flow kept dying: the pasted OAuth code rendered late or not at all, and
     * the single-use code was burned. Sign in must therefore start the guided
     * flow — the external window survives only as the failure fallback below.
     */
    const fresh = account({
      id: "claude:new1",
      label: "Fresh seat",
      builtin: false,
      connected: false,
      mode: "unknown",
      message: "Not signed in yet.",
      email: null,
      tier: null,
    });
    vi.mocked(fetchAgentAccounts).mockResolvedValue(
      response([account(), fresh]) as never,
    );
    vi.mocked(startLoginFlow).mockResolvedValue(flowState());
    vi.mocked(getLoginFlow).mockResolvedValue(flowState());
    vi.mocked(submitLoginFlowCode).mockResolvedValue(
      flowState({ status: "verifying", code_expected: true }),
    );
    render(<AgentAccountsPanel />);
    await waitFor(() => expect(screen.getByText("Fresh seat")).toBeTruthy());

    const row = screen.getByText("Fresh seat").closest("li")!;
    expect(row.textContent).toContain("Not signed in");
    fireEvent.click(screen.getByText("Sign in"));

    await waitFor(() => {
      expect(startLoginFlow).toHaveBeenCalledWith("claude:new1");
    });
    // The link is shown as DATA — with a second subscription the default
    // browser is signed in as the wrong account, so the URL must be copyable
    // into a private window, not auto-opened and gone.
    const url = await screen.findByTestId("login-flow-url");
    expect(url.textContent).toContain("claude.ai/oauth/authorize");
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(openExternalUrl).toHaveBeenCalledWith(
      "https://claude.ai/oauth/authorize?client_id=abc&state=xyz",
    );

    fireEvent.change(
      screen.getByPlaceholderText("Paste the code from the browser here"),
      { target: { value: "  code#state  " } },
    );
    fireEvent.click(screen.getByText("Confirm code"));
    await waitFor(() => {
      expect(submitLoginFlowCode).toHaveBeenCalledWith("flow-1", "code#state");
    });
  });

  it("falls back to the terminal window only after the in-app flow failed", async () => {
    const fresh = account({
      id: "claude:new1",
      label: "Fresh seat",
      builtin: false,
      connected: false,
      mode: "unknown",
      message: "Not signed in yet.",
      email: null,
      tier: null,
    });
    vi.mocked(fetchAgentAccounts).mockResolvedValue(
      response([account(), fresh]) as never,
    );
    vi.mocked(startLoginFlow).mockResolvedValue(
      flowState({
        status: "failed",
        finished: true,
        code_expected: false,
        url: null,
        message: "OAuth error: Invalid code",
      }),
    );
    vi.mocked(loginAgentAccount).mockResolvedValue({
      message: "Sign-in started",
    } as never);
    render(<AgentAccountsPanel />);
    await waitFor(() => expect(screen.getByText("Fresh seat")).toBeTruthy());

    fireEvent.click(screen.getByText("Sign in"));
    await waitFor(() =>
      expect(screen.getByText("OAuth error: Invalid code")).toBeTruthy(),
    );
    fireEvent.click(screen.getByText("Use a terminal window instead"));
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

  it("renders a CLI this file has never heard of, using the name the API sent", async () => {
    /*
     * The regression this whole panel needed a guard for. It used to map over a
     * list of platform ids written here, so a CLI the BACKEND supports was
     * simply not drawn — and nothing failed: the payload was right, the fetch
     * succeeded, and the section was absent. A test that only ever feeds it the
     * two ids it already knows would pass with the feature invisible.
     */
    vi.mocked(fetchAgentAccounts).mockResolvedValue({
      platforms: [
        {
          platform: "acme",
          display_name: "Acme Coder",
          active_account: "acme:default",
          accounts: [
            account({
              id: "acme:default",
              platform: "acme",
              label: "Default Acme login",
              config_dir: "/home/u/.acme",
            }),
          ],
        },
      ],
    } as never);
    render(<AgentAccountsPanel />);

    // The heading comes from the payload, not from a label map here.
    await waitFor(() => expect(screen.getByText("Acme Coder")).toBeTruthy());
    expect(screen.getByText("Default Acme login")).toBeTruthy();
  });

  it("falls back to the platform id when an older backend sends no name", async () => {
    vi.mocked(fetchAgentAccounts).mockResolvedValue({
      platforms: [
        {
          platform: "acme",
          active_account: "acme:default",
          accounts: [
            account({ id: "acme:default", platform: "acme", label: "Only seat" }),
          ],
        },
      ],
    } as never);
    render(<AgentAccountsPanel />);
    await waitFor(() => expect(screen.getByText("acme")).toBeTruthy());
  });
});
