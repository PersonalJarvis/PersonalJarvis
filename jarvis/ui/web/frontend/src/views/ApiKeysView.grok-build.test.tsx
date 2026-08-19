/**
 * Component tests for Grok Build in ApiKeysView.
 *
 * Grok Build is an xAI-subscription worker for Subagents only. It must not
 * render as an activatable main Brain card; the SuperGrok login and active
 * toggle live in the Subagent section.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

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

interface RouteResult {
  status?: number;
  body: unknown;
}

function installFetchMock(routes: Record<string, () => RouteResult>) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    let body: unknown = null;
    if (typeof init?.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    }
    calls.push({ url, method, body });
    const prefixes = Object.keys(routes).sort((a, b) => b.length - a.length);
    for (const prefix of prefixes) {
      if (url.startsWith(prefix)) {
        const { status = 200, body: resBody } = routes[prefix]();
        return {
          ok: status >= 200 && status < 300,
          status,
          statusText: status >= 200 && status < 300 ? "OK" : "ERR",
          json: async () => resBody,
          text: async () => JSON.stringify(resBody),
        } as Response;
      }
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  Object.assign(navigator, {
    clipboard: { writeText: vi.fn(async () => undefined) },
  });
  return { fetchMock, calls };
}

function grokBuildDescriptor(overrides: Record<string, unknown> = {}) {
  return {
    id: "grok-build",
    label: "Grok Build (xAI subscription)",
    tier: "brain",
    auth_mode: "grok_build",
    secret_keys: [],
    secrets_set: {},
    dashboard_url: null,
    login_cli: ["grok", "login"],
    install_hint: "irm https://x.ai/cli/install.ps1 | iex",
    credential_path_hint: "~/.grok/auth.json",
    configured: true,
    active: false,
    brain_switchable: false,
    cli_installed: true,
    grok_build_status: {
      installed: true,
      connected: true,
      mode: "subscription",
      message: "Connected via SuperGrok / X Premium+",
      version: "grok 0.2.0",
      user_email: "ada@example.com",
      binary_path: "grok",
      error: null,
    },
    ...overrides,
  };
}

const JARVIS_AGENT_EMPTY = {
  configured: true,
  enabled: false,
  binary_path: "",
  binary_detected: null,
  version_pin: null,
  time_cap_min: null,
  concurrency: null,
  state_dir_root: null,
  brain_primary: "gemini",
  provider_slug: null,
  model_override: null,
  model_resolved: null,
  mapping: [
    {
      jarvis: "grok-build",
      openclaw: "grok-cli (direct)",
      env_var: "SuperGrok-OAuth",
      env_fallback: null,
      key_set: true,
      api_key_set: false,
      dedicated_key_set: false,
      shared_key_set: false,
      oauth_connected: true,
      credential_source: "oauth",
      secret_key: null,
      dashboard_url: null,
      credential_help: null,
      is_active_brain: false,
      billing: "subscription",
      label: "Grok Build",
    },
  ],
};

const TELEPHONY_STATUS_EMPTY = {
  available: false,
  configured: false,
  enabled: false,
  account_sid_masked: "",
  phone_number: "",
  public_base_url: "",
  webhook_url: "",
  auth_token_set: false,
  twilio_reachable: false,
  twilio_error: null,
  tts_provider: "",
  tts_voice: "",
  active_calls: 0,
  max_call_seconds: 600,
};
const TELEPHONY_CONFIG_EMPTY = {
  enabled: false,
  account_sid: "",
  phone_number: "",
  public_base_url: "",
  greeting: "",
  language_code: "de-DE",
  fallback_mode: "media",
  max_call_seconds: 600,
  auth_token_set: false,
};

function routesFor(
  provider: Record<string, unknown>,
): Record<string, () => RouteResult> {
  return {
    "/api/providers": () => ({ body: { providers: [provider] } }),
    "/api/jarvis-agent/status": () => ({ body: JARVIS_AGENT_EMPTY }),
    "/api/codex/status": () => ({ body: {} }),
    "/api/antigravity/status": () => ({ body: {} }),
    "/api/claude/status": () => ({ body: {} }),
    "/api/grok-build/status": () => ({
      body: provider.grok_build_status ?? {},
    }),
    "/api/jarvis-agent/switch": () => ({
      body: { ok: true, active: "grok-build", persisted: true },
    }),
    "/api/grok-build/login": () => ({
      body: { ok: true, pid: 123, message: "Grok Build login was started" },
    }),
    "/api/grok-build/logout": () => ({
      body: { ok: true, message: "Grok Build was disconnected" },
    }),
    "/api/telephony/status": () => ({ body: TELEPHONY_STATUS_EMPTY }),
    "/api/telephony/config": () => ({ body: TELEPHONY_CONFIG_EMPTY }),
    "/api/telephony/scripts": () => ({ body: { scripts: [] } }),
    "/api/telephony/calls": () => ({ body: { calls: [] } }),
  };
}

function grokBuildNotConnected() {
  return grokBuildDescriptor({
    grok_build_status: {
      installed: true,
      connected: false,
      mode: "unknown",
      message: "Grok Build login not connected",
      version: "grok 0.2.0",
      user_email: null,
      binary_path: "grok",
      error: null,
    },
  });
}

describe("ApiKeysView — Grok Build (xAI subscription) OAuth card", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders the connected subscription card with email AND a Set-active radio", async () => {
    installFetchMock(routesFor(grokBuildDescriptor()));
    render(<ApiKeysView />);
    fireEvent.click(screen.getByRole("tab", { name: /-agents$/i }));
    await waitFor(() => expect(screen.getByText("Grok Build")).toBeTruthy());
    expect(screen.getByText(/ada@example\.com/)).toBeTruthy();
    expect(screen.getByRole("radio")).toBeTruthy();
  });

  it("switches the subagent to Grok Build when connected", async () => {
    const { calls } = installFetchMock(routesFor(grokBuildDescriptor()));
    render(<ApiKeysView />);
    fireEvent.click(screen.getByRole("tab", { name: /-agents$/i }));
    await waitFor(() => screen.getByRole("radio"));
    fireEvent.click(screen.getByRole("radio"));
    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.url.startsWith("/api/jarvis-agent/switch") &&
            c.method === "POST" &&
            (c.body as { provider?: string })?.provider === "grok-build",
        ),
      ).toBe(true),
    );
  });

  it("shows the Connect button while not logged in and starts login", async () => {
    const mapping = {
      ...JARVIS_AGENT_EMPTY,
      mapping: [
        {
          ...JARVIS_AGENT_EMPTY.mapping[0],
          key_set: false,
          oauth_connected: false,
        },
      ],
    };
    const { calls } = installFetchMock({
      ...routesFor(grokBuildNotConnected()),
      "/api/jarvis-agent/status": () => ({ body: mapping }),
    });
    render(<ApiKeysView />);
    fireEvent.click(screen.getByRole("tab", { name: /-agents$/i }));
    await waitFor(() => expect(screen.getByText("Connect")).toBeTruthy());
    fireEvent.click(screen.getByText("Connect"));
    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.url.startsWith("/api/grok-build/login") && c.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("disconnects via POST /api/grok-build/logout", async () => {
    const { calls } = installFetchMock(routesFor(grokBuildDescriptor()));
    render(<ApiKeysView />);
    fireEvent.click(screen.getByRole("tab", { name: /-agents$/i }));
    const disconnect = await waitFor(() => screen.getByText("Disconnect"));
    fireEvent.click(disconnect);
    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.url.startsWith("/api/grok-build/logout") && c.method === "POST",
        ),
      ).toBe(true),
    );
  });
});
