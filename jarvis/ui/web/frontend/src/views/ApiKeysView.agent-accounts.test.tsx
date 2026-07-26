/**
 * The subscription switcher REACHES the Settings page.
 *
 * Deliberately separate from AgentAccountsPanel.test.tsx, which renders the
 * panel on its own. A component that works in isolation and is wired nowhere is
 * the most common way a finished feature stays invisible — and it looks exactly
 * like a working feature from the test suite's side. This renders the real
 * API-Keys view and asserts the panel is in it, with the account rows the
 * backend hands over.
 *
 * Note the tab click: the switcher lives under the Agents category, not on the
 * page the view opens on. That is worth pinning too — "it is in the build" and
 * "the user can reach it" are different claims.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiKeysView } from "@/views/ApiKeysView";

vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: "pipeline",
    realtimeAvailable: false,
    setMode: vi.fn(),
    isLoading: false,
    isSaving: false,
  }),
}));

const ACCOUNTS = {
  platforms: [
    {
      platform: "claude",
      active_account: "claude:seat2",
      accounts: [
        {
          id: "claude:default",
          platform: "claude",
          label: "Default Claude Code login",
          config_dir: "/home/u/.claude",
          builtin: true,
          connected: true,
          mode: "subscription",
          message: "Signed in via Claude Max (one@example.com).",
          email: "one@example.com",
          tier: "max",
        },
        {
          id: "claude:seat2",
          platform: "claude",
          label: "Max seat 2",
          config_dir: "/home/u/.jarvis/agent-accounts/claude/seat2",
          builtin: false,
          connected: true,
          mode: "subscription",
          message: "Signed in via Claude Max (two@example.com).",
          email: "two@example.com",
          tier: "max",
        },
      ],
    },
    {
      platform: "codex",
      active_account: "codex:default",
      accounts: [
        {
          id: "codex:default",
          platform: "codex",
          label: "Default Codex login",
          config_dir: "/home/u/.codex",
          builtin: true,
          connected: false,
          mode: "unknown",
          message: "Not signed in — use Sign in to connect this Codex plan.",
          email: null,
          tier: null,
        },
      ],
    },
  ],
};

const SECTION_HEALTH = {
  sections: {
    brain: { status: "unknown", reason: "unknown", detail: "", subject_id: null },
    "computer-use": { status: "unknown", reason: "unknown", detail: "", subject_id: null },
    tts: { status: "unknown", reason: "unknown", detail: "", subject_id: null },
    stt: { status: "unknown", reason: "unknown", detail: "", subject_id: null },
    realtime: { status: "unknown", reason: "unknown", detail: "", subject_id: null },
    subagents: { status: "unknown", reason: "unknown", detail: "", subject_id: null },
    advanced: { status: "unknown", reason: "unknown", detail: "", subject_id: null },
  },
  checked_at: 0,
  cached: false,
};

function installFetchMock() {
  const routes: Record<string, unknown> = {
    "/api/agent-accounts": ACCOUNTS,
    "/api/providers/section-health": SECTION_HEALTH,
    "/api/providers": { providers: [] },
    "/api/jarvis-agent/status": { mapping: [], brain_primary: "" },
    "/api/codex/status": { installed: true, connected: true, mode: "chatgpt" },
    "/api/claude/status": { installed: true, connected: true, mode: "subscription" },
    "/api/antigravity/status": { installed: false, connected: false, mode: "unknown" },
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const prefix = Object.keys(routes)
      .sort((a, b) => b.length - a.length)
      .find((p) => url.startsWith(p));
    const body = prefix ? routes[prefix] : {};
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as Response;
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/** Open the category the switcher lives in — it is not the default tab. */
async function openAgentsTab() {
  const tab = await waitFor(() => screen.getByRole("tab", { name: /Agents/i }));
  fireEvent.click(tab);
}

describe("ApiKeysView — the subscription switcher is wired in", () => {
  it("renders the switcher inside the settings page", async () => {
    installFetchMock();
    render(<ApiKeysView />);
    await openAgentsTab();
    await waitFor(() => {
      expect(screen.getByText("Your subscriptions")).toBeTruthy();
    });
  });

  it("shows both CLIs' accounts, with the active one marked per CLI", async () => {
    installFetchMock();
    render(<ApiKeysView />);
    await openAgentsTab();
    await waitFor(() => expect(screen.getByText("Max seat 2")).toBeTruthy());

    expect(screen.getByText("Default Claude Code login")).toBeTruthy();
    expect(screen.getByText("Default Codex login")).toBeTruthy();
    // One "in use" per platform: the second Claude seat, and Codex's default.
    expect(screen.getAllByText("in use").length).toBe(2);
    expect(screen.getByText("Max seat 2").closest("li")?.textContent).toContain(
      "in use",
    );
  });

  it("keeps the unsigned account honest, with the way in offered", async () => {
    installFetchMock();
    render(<ApiKeysView />);
    await openAgentsTab();
    await waitFor(() => expect(screen.getByText("Default Codex login")).toBeTruthy());
    const row = screen.getByText("Default Codex login").closest("li")!;
    expect(row.textContent).toContain("Not signed in");
    expect(row.querySelector("button")).toBeTruthy();
  });
});
