import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

import { SubscriptionAccounts } from "./SubscriptionAccounts";

const account = (id: string, label: string, email: string, extra: Record<string, unknown> = {}) => ({
  id,
  platform: "claude",
  label,
  config_dir: "",
  builtin: id === "claude:default",
  connected: true,
  mode: "subscription",
  message: "",
  email,
  tier: "max",
  ...extra,
});

let accounts = [
  account("claude:default", "Default Claude Code login", "me@example.com"),
  account("claude:two", "Second", "two@example.com"),
];
let calls: { url: string; method: string; body: unknown }[] = [];

beforeEach(() => {
  calls = [];
  accounts = [
    account("claude:default", "Default Claude Code login", "me@example.com"),
    account("claude:two", "Second", "two@example.com"),
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
      const list = () => ({ platforms: [{ platform: "claude", display_name: "Claude Code", active_account: "claude:two", accounts }] });
      let body: unknown = {};
      if (url.startsWith("/api/agent-accounts/usage")) {
        body = {
          ttl_seconds: 60,
          generated_at: 0,
          accounts: [
            { account_id: "claude:default", platform: "claude", status: "ok", source: "live", as_of: 0, message: "", plan: "Max 20x",
              windows: [{ kind: "session", percent: 65.4, severity: "normal", resets_at: null, window_minutes: null, scope_label: null, raw_label: null }] },
            { account_id: "claude:two", platform: "claude", status: "ok", source: "live", as_of: 0, message: "", plan: "Max 20x",
              windows: [{ kind: "session", percent: 100, severity: "critical", resets_at: null, window_minutes: null, scope_label: null, raw_label: null }] },
          ],
        };
      } else if (url.includes("/login-flow")) {
        body = { flow: { flow_id: "f1", account_id: "claude:new", platform: "claude", label: "Work", status: "starting", url: null, code_expected: false, message: "", tail: "", finished: false } };
      } else if (url === "/api/agent-accounts" && method === "POST") {
        accounts = [...accounts, account("claude:new", "Work", "", { connected: false, mode: "unknown" })];
        body = list();
      } else if (url.startsWith("/api/agent-accounts")) {
        body = list();
      }
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SubscriptionAccounts", () => {
  it("lists every connected seat with plan, usage and the active one", async () => {
    render(<SubscriptionAccounts kind="claude_cli" />);
    const first = await screen.findByTestId("subscription-account-row-claude:default");
    expect(first.textContent).toContain("me@example.com");
    await waitFor(() => expect(first.textContent).toContain("65%"));
    expect(first.textContent).toContain("Max 20x");
    const second = screen.getByTestId("subscription-account-row-claude:two");
    expect(within(second).getByText("providers_page.account_active")).toBeTruthy();
    await waitFor(() => expect(second.textContent).toContain("100%"));
  });

  it("adds a seat and starts its sign-in right away", async () => {
    render(<SubscriptionAccounts kind="claude_cli" />);
    await screen.findByTestId("subscription-account-row-claude:default");
    fireEvent.click(screen.getByTestId("subscription-account-add"));
    fireEvent.change(screen.getByLabelText("agent_accounts.name_placeholder"), { target: { value: "Work" } });
    fireEvent.click(screen.getByText("agent_accounts.add_confirm"));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.url === "/api/agent-accounts")?.body).toEqual({ platform: "claude", label: "Work" }),
    );
    await waitFor(() => expect(calls.some((c) => c.url === "/api/agent-accounts/claude%3Anew/login-flow")).toBe(true));
  });

  it("stays away for a CLI with a single login", () => {
    const { container } = render(<SubscriptionAccounts kind="antigravity" />);
    expect(container.innerHTML).toBe("");
  });
});
