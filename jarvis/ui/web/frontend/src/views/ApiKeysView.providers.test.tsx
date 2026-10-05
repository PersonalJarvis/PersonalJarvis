/**
 * The provider page: a Live calls tab on OpenAI GPT-Live, and an Agents tab
 * with one entry per company — any number on at once, each reached by its
 * subscription or its key — beside the selected company's settings.
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
vi.mock("@/components/AgentAccountsPanel", () => ({ AgentAccountsPanel: () => null }));
vi.mock("@/components/PromptWriterCard", () => ({ PromptWriterCard: () => null }));
vi.mock("@/components/SubagentModelCard", () => ({ SubagentModelCard: () => null }));
// GPT-Live's own form has its own tests; here it only has to place the key row.
vi.mock("@/components/providers/LiveProfile", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/providers/LiveProfile")>()),
  LiveProfile: ({ keyField }: { keyField?: React.ReactNode }) => <div data-testid="live-profile">{keyField}</div>,
}));

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
    { jarvis: "openai", label: "OpenAI", key_set: false, is_active_brain: false, billing: "api" },
    { jarvis: "gemini", label: "Google Gemini", key_set: true, is_active_brain: true, billing: "api" },
  ],
};

const CLAUDE_STATUS = {
  installed: true, connected: true, mode: "subscription", message: "Connected",
  user_email: "me@example.com", account_label: "Claude Max", subscription_type: "max",
  version: "2.1.0", binary_path: "/usr/bin/claude",
};

const CATALOG = {
  providers: [
    {
      id: "claude-api", label: "Claude", family: "claude", runner: "claude-cli", models_source: "curated",
      curated_models: [{ id: "opus", label: "Opus" }, { id: "sonnet", label: "Sonnet" }],
      default_model: "", keyless: false, native_resume: true, effort_levels: [], default_effort: "",
      permission_modes: [], default_permission_mode: "", cli_installed: true, enabled: true, hidden_models: [],
    },
  ],
  default_cwd: "",
  shell: "",
};

const LIVE = {
  profile: {
    auth_mode: "api_key", model: "gpt-live-1", voice: "gleam", backend_model: "", reasoning_effort: "medium",
    web_search: true, instructions: "", backend_instructions: "", configured: false,
  },
  key_ready: false, active: false, agent_configured: false,
};

let calls: { url: string; method: string; body: unknown }[] = [];
let prefs: { disabled: string[]; api_only: string[]; hidden_models: Record<string, string[]> };

beforeEach(() => {
  calls = [];
  prefs = { disabled: [], api_only: [], hidden_models: {} };
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
    "/api/society/provider-prefs": prefs,
    "/api/agent-chat/catalog": CATALOG,
    "/api/live/profile": LIVE,
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

function openAgents() {
  fireEvent.click(screen.getByRole("tab", { name: "Agents" }));
}

beforeEach(() => {
  try {
    localStorage.removeItem("jarvis.apikeys.tab");
  } catch {
    /* no storage in this environment */
  }
});

describe("ApiKeysView — Live calls", () => {
  it("opens on live calls with GPT-Live and one OpenAI key row", async () => {
    renderPage();
    const voice = await screen.findByTestId("apikeys-voice");
    expect(within(voice).getByTestId("live-profile")).toBeTruthy();
    expect(await within(voice).findByTestId("voice-key")).toBeTruthy();
  });

  it("has only the two tabs", async () => {
    renderPage();
    await screen.findByTestId("apikeys-voice");
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Live calls", "Agents"]);
  });
});

describe("ApiKeysView — Agents", () => {
  it("lists each company once with its subscription account", async () => {
    renderPage();
    await screen.findByTestId("apikeys-voice");
    openAgents();
    const claude = await screen.findByTestId("agent-provider-claude-api");
    expect(claude.textContent).toContain("Anthropic");
    expect(claude.textContent).toContain("me@example.com · Claude Max");
    expect(claude.textContent).toContain("v2.1.0");
    expect(await screen.findByTestId("agent-provider-gemini")).toBeTruthy();
  });

  it("turns several companies on at once instead of switching between them", async () => {
    Object.assign(prefs, { disabled: ["claude-api"], api_only: [] });
    renderPage();
    await screen.findByTestId("apikeys-voice");
    openAgents();
    const claude = await screen.findByTestId("agent-provider-claude-api");
    const gemini = await screen.findByTestId("agent-provider-gemini");
    await waitFor(() => expect(within(gemini).getByRole("switch").getAttribute("aria-checked")).toBe("true"));
    await waitFor(() => expect((within(claude).getByRole("switch") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(within(claude).getByRole("switch"));
    await waitFor(() => {
      const put = calls.find((c) => c.method === "PUT" && c.url === "/api/society/provider-prefs");
      expect(put?.body).toEqual({ disabled: [] });
    });
    // Gemini stays the default worker; turning Claude on never turns it off.
    expect(calls.some((c) => c.url === "/api/jarvis-agent/switch")).toBe(false);
  });

  it("switches a company between its subscription and its key in one place", async () => {
    renderPage();
    await screen.findByTestId("apikeys-voice");
    openAgents();
    fireEvent.click(await screen.findByRole("button", { name: "Anthropic" }));
    const access = await screen.findByTestId("agent-provider-access");
    await waitFor(() =>
      expect(within(access).getByRole("radio", { name: "Subscription" }).getAttribute("aria-checked")).toBe("true"),
    );
    expect(screen.queryByTestId("provider-key-input")).toBeNull();
    fireEvent.click(within(access).getByRole("radio", { name: "API key" }));
    await waitFor(() => {
      const put = calls.find((c) => c.method === "PUT" && c.url === "/api/society/provider-prefs");
      expect(put?.body).toEqual({ api_only: ["claude-api"], disabled: [] });
    });
  });

  it("saves one key for the whole company", async () => {
    Object.assign(prefs, { disabled: [], api_only: ["claude-api"] });
    renderPage();
    await screen.findByTestId("apikeys-voice");
    openAgents();
    fireEvent.click(await screen.findByRole("button", { name: "Anthropic" }));
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
    await screen.findByTestId("apikeys-voice");
    openAgents();
    fireEvent.click(await screen.findByRole("button", { name: "Google Gemini" }));
    const row = await screen.findByTestId("provider-separate-key-realtime_gemini_api_key");
    fireEvent.click(within(row).getByRole("button"));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/secrets/realtime_gemini_api_key")).toBe(true),
    );
  });

  it("hides a model from the agents with its switch", async () => {
    renderPage();
    await screen.findByTestId("apikeys-voice");
    openAgents();
    fireEvent.click(await screen.findByRole("button", { name: "Anthropic" }));
    const models = await screen.findByTestId("agent-models-claude-api");
    await waitFor(() =>
      expect((within(models).getByRole("switch", { name: "Sonnet" }) as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.click(within(models).getByRole("switch", { name: "Sonnet" }));
    await waitFor(() => {
      const put = calls.find((c) => c.method === "PUT" && c.url === "/api/society/provider-prefs");
      expect(put?.body).toEqual({ hidden_models: { "claude-api": ["sonnet"] } });
    });
  });

  it("shows the sign-in with the account and the binary behind it", async () => {
    renderPage();
    await screen.findByTestId("apikeys-voice");
    openAgents();
    fireEvent.click(await screen.findByRole("button", { name: "Anthropic" }));
    const detail = await screen.findByTestId("agent-provider-detail-claude-api");
    await waitFor(() => expect(detail.textContent).toContain("me@example.com"));
    expect(within(detail).getByTestId("subscription-disconnect")).toBeTruthy();
    fireEvent.click(within(detail).getByRole("button", { name: "Runtime" }));
    expect((within(detail).getByLabelText("Binary path") as HTMLInputElement).value).toBe("/usr/bin/claude");
  });
});
