/**
 * The provider page: every company once, one key per company, and the jobs it
 * powers switchable where they are shown.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: "realtime",
    realtimeAvailable: true,
    statusKnown: true,
    setMode: vi.fn(),
    isLoading: false,
    isSaving: false,
    sessionActive: false,
  }),
}));

// The sections below the providers own their data sources and have their own
// tests; here they only need to stay out of the way.
vi.mock("@/views/settings/WikiProviderCard", () => ({ WikiProviderCard: () => null }));
vi.mock("@/views/settings/JarvisApiGroup", () => ({ JarvisApiGroup: () => null }));
vi.mock("@/views/settings/TeamProxyGroup", () => ({ TeamProxyGroup: () => null }));
vi.mock("@/views/TelephonyView", () => ({ TelephonyPanel: () => null }));
vi.mock("@/components/AgentAccountsPanel", () => ({ AgentAccountsPanel: () => null }));
vi.mock("@/components/PromptWriterCard", () => ({ PromptWriterCard: () => null }));
vi.mock("@/components/SubagentModelCard", () => ({ SubagentModelCard: () => null }));

import { _resetProvidersCacheForTests } from "@/hooks/useProviders";
import { ApiKeysView } from "@/views/ApiKeysView";

const PROVIDERS = [
  { id: "claude-api", label: "Claude (API-Key)", tier: "brain", auth_mode: "api_key", secret_keys: ["anthropic_api_key"], secrets_set: {}, configured: false, active: false, billing: "api" },
  { id: "claude-cli", label: "Claude (Anthropic subscription)", tier: "brain", auth_mode: "claude_cli", secret_keys: [], secrets_set: {}, configured: true, active: false, billing: "subscription" },
  { id: "gemini", label: "Google Gemini", tier: "brain", auth_mode: "api_key", secret_keys: ["gemini_api_key"], secrets_set: { gemini_api_key: true }, configured: true, active: false, billing: "api" },
  { id: "gemini-live", label: "Gemini Live", tier: "realtime", auth_mode: "api_key", secret_keys: ["realtime_gemini_api_key"], secrets_set: {}, configured: true, active: true, billing: "api" },
].map((p) => ({ dashboard_url: null, login_cli: null, install_hint: null, credential_path_hint: null, cli_installed: null, credential_help: null, signup_url: null, alt_credential: null, ...p }));

const FAMILIES = [
  {
    id: "claude-api", label: "Anthropic", logo_id: "claude-api", local: false,
    key_slot: "anthropic_api_key", key_present: false, separate_keys: [],
    dashboard_url: "https://console.anthropic.com/settings/keys", signup_url: null,
    subscription: { kind: "claude_cli", provider_id: "claude-cli", label: "Claude (Anthropic subscription)" },
    provider_ids: ["claude-api", "claude-cli"], hidden_ids: [], agent_ids: ["claude-api", "claude-cli"],
  },
  {
    id: "gemini", label: "Google Gemini", logo_id: "gemini", local: false,
    key_slot: "gemini_api_key", key_present: true,
    separate_keys: [{ slot: "realtime_gemini_api_key", surface: "live_voice" }],
    dashboard_url: null, signup_url: null, subscription: null,
    provider_ids: ["gemini", "gemini-live"], hidden_ids: [], agent_ids: ["gemini", "gemini-live"],
  },
];

const AGENTS = {
  configured: true, enabled: true, binary_path: "", binary_detected: null, version_pin: null,
  time_cap_min: null, concurrency: null, state_dir_root: null, brain_primary: "gemini",
  provider_slug: null, model_override: null, sub_model_override: "", model_resolved: null,
  mapping: [
    { jarvis: "claude-api", label: "Claude", key_set: true, is_active_brain: false, billing: "subscription_or_api" },
    { jarvis: "gemini", label: "Google Gemini", key_set: true, is_active_brain: true, billing: "api" },
  ],
};

const CLAUDE_STATUS = {
  installed: true, connected: true, mode: "subscription", message: "Connected",
  user_email: "me@example.com", account_label: "Claude Max", subscription_type: "max",
  version: "2.1.0", binary_path: "/usr/bin/claude",
};

let calls: { url: string; method: string; body: unknown }[] = [];

beforeEach(() => {
  calls = [];
  _resetProvidersCacheForTests();
  const routes: Record<string, unknown> = {
    "/api/providers/families": { families: FAMILIES },
    "/api/providers/section-health": { sections: {}, checked_at: 0, cached: false },
    "/api/providers": { providers: PROVIDERS },
    "/api/jarvis-agent/status": AGENTS,
    "/api/jarvis-agent/switch": { ok: true },
    "/api/claude/status": CLAUDE_STATUS,
    "/api/codex/status": null,
    "/api/secrets/": { ok: true, key: "x", written: ["anthropic_api_key"] },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
      const match = Object.keys(routes)
        .sort((a, b) => b.length - a.length)
        .find((prefix) => url.startsWith(prefix));
      const body = match ? routes[match] : {};
      const ok = body !== null;
      return { ok, status: ok ? 200 : 404, json: async () => body ?? {}, text: async () => "" } as Response;
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ApiKeysView />
    </QueryClientProvider>,
  );
}

describe("ApiKeysView — one entry per company", () => {
  it("lists every company once, with how it is connected", async () => {
    renderPage();
    const list = await screen.findByTestId("provider-family-list");
    expect(within(list).getByTestId("provider-family-claude-api").textContent).toContain("Anthropic");
    expect(within(list).getByTestId("provider-family-claude-api").textContent).toContain("me@example.com");
    expect(within(list).getByTestId("provider-family-gemini").textContent).toContain("API key");
  });

  it("opens on the company that carries the voice and tags what it powers", async () => {
    renderPage();
    const detail = await screen.findByTestId("provider-family-detail-gemini");
    expect(within(detail).getByTestId("provider-use-voice")).toBeTruthy();
    expect(within(detail).getByTestId("provider-use-agents")).toBeTruthy();
  });

  it("saves one key for the whole company", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("provider-family-claude-api"));
    const input = await screen.findByTestId("provider-key-input");
    fireEvent.change(input, { target: { value: "sk-ant-api03-test" } });
    fireEvent.click(screen.getByTestId("provider-key-save"));
    await waitFor(() => {
      const save = calls.find((c) => c.method === "POST" && c.url === "/api/secrets/anthropic_api_key");
      expect(save?.body).toEqual({ value: "sk-ant-api03-test", scope: "everywhere" });
    });
  });

  it("moves a feature with its own key back onto the main key", async () => {
    renderPage();
    const row = await screen.findByTestId("provider-separate-key-realtime_gemini_api_key");
    fireEvent.click(within(row).getByRole("button"));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/secrets/realtime_gemini_api_key")).toBe(true),
    );
  });

  it("switches the agents onto another company in one click", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("provider-family-claude-api"));
    fireEvent.click(await screen.findByTestId("provider-agent-use-claude-api"));
    await waitFor(() => {
      const post = calls.find((c) => c.url === "/api/jarvis-agent/switch");
      expect(post?.body).toMatchObject({ provider: "claude-api" });
    });
  });

  it("shows the subscription sign-in with the account and the CLI behind it", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("provider-family-claude-api"));
    const section = await screen.findByTestId("provider-subscription");
    expect(section.textContent).toContain("me@example.com");
    expect(section.textContent).toContain("2.1.0");
    expect(within(section).getByTestId("provider-subscription-disconnect")).toBeTruthy();
  });
});
