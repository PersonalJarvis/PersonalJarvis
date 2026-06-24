/**
 * Component tests for Codex in ApiKeysView.
 *
 * Codex is a ChatGPT/Codex-login worker for Subagents only. It must not render
 * as an activatable main Brain card; the login and active toggle live in the
 * Subagent section.
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
  return { calls };
}

function codexStatus(overrides: Record<string, unknown> = {}) {
  return {
    installed: true,
    connected: true,
    mode: "chatgpt",
    message: "Connected via ChatGPT (chatgpt-user@example.com).",
    version: "codex-cli 0.137.0",
    account_label: "ChatGPT/Codex-Login",
    user_email: "chatgpt-user@example.com",
    binary_path: "codex",
    error: null,
    ...overrides,
  };
}

function codexDescriptor(overrides: Record<string, unknown> = {}) {
  return {
    id: "codex",
    label: "OpenAI Codex",
    tier: "brain",
    auth_mode: "codex",
    secret_keys: ["codex_openai_api_key"],
    secrets_set: { codex_openai_api_key: false },
    dashboard_url: "https://platform.openai.com/api-keys",
    login_cli: ["codex", "login"],
    install_hint: "npm i -g @openai/codex",
    credential_path_hint: null,
    configured: true,
    active: false,
    brain_switchable: false,
    cli_installed: true,
    codex_brain_ready: true,
    codex_status: codexStatus(),
    ...overrides,
  };
}

function codexDescriptorNotConnected() {
  return codexDescriptor({
    configured: false,
    codex_status: codexStatus({
      connected: false,
      mode: "not_connected",
      message: "Codex is installed but not logged in.",
      account_label: null,
      user_email: null,
    }),
  });
}

const OPENCLAW_CODEX = {
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
  sub_model_override: null,
  model_resolved: null,
  mapping: [
    {
      jarvis: "openai-codex",
      openclaw: "codex-cli (direct)",
      env_var: "ChatGPT-OAuth",
      env_fallback: "OPENAI_API_KEY",
      key_set: true,
      is_active_brain: false,
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
    "/api/openclaw/status": () => ({ body: OPENCLAW_CODEX }),
    "/api/codex/status": () => ({
      body: provider.codex_status ?? {},
    }),
    "/api/antigravity/status": () => ({ body: {} }),
    "/api/subagent/switch": () => ({
      body: { ok: true, active: "openai-codex", persisted: true },
    }),
    "/api/codex/login": () => ({
      body: { ok: true, pid: 123, message: "Codex login was started" },
    }),
    "/api/codex/logout": () => ({
      body: { ok: true, message: "Codex was disconnected" },
    }),
    "/api/brain/switch": () => ({
      status: 500,
      body: { detail: "Codex must not use brain switch" },
    }),
    "/api/telephony/status": () => ({ body: TELEPHONY_STATUS_EMPTY }),
    "/api/telephony/config": () => ({ body: TELEPHONY_CONFIG_EMPTY }),
    "/api/telephony/scripts": () => ({ body: { scripts: [] } }),
    "/api/telephony/calls": () => ({ body: { calls: [] } }),
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ApiKeysView - Codex is subagent-only", () => {
  it("does not render Codex as a Brain provider card", async () => {
    installFetchMock(routesFor(codexDescriptor()));
    render(<ApiKeysView />);

    await waitFor(() => expect(screen.getByText("OpenAI Codex ChatGPT login")).toBeTruthy());

    expect(screen.queryByText("ChatGPT / Codex login")).toBeNull();
    expect(screen.getByText(/chatgpt-user@example\.com/)).toBeTruthy();
  });

  it("switches the subagent to Codex without calling brain switch", async () => {
    const { calls } = installFetchMock(routesFor(codexDescriptor()));
    render(<ApiKeysView />);

    await waitFor(() => screen.getByRole("radio"));
    fireEvent.click(screen.getByRole("radio"));

    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.url.startsWith("/api/subagent/switch") &&
            c.method === "POST" &&
            (c.body as { provider?: string })?.provider === "openai-codex",
        ),
      ).toBe(true),
    );
    expect(calls.some((c) => c.url.startsWith("/api/brain/switch"))).toBe(false);
  });

  it("shows the Connect button while not logged in and starts login", async () => {
    const { calls } = installFetchMock(routesFor(codexDescriptorNotConnected()));
    render(<ApiKeysView />);

    const connect = await waitFor(() => screen.getByText("Connect"));
    fireEvent.click(connect);

    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.url.startsWith("/api/codex/login") && c.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("disconnects via POST /api/codex/logout", async () => {
    const { calls } = installFetchMock(routesFor(codexDescriptor()));
    render(<ApiKeysView />);

    const disconnect = await waitFor(() => screen.getByText("Disconnect"));
    fireEvent.click(disconnect);

    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.url.startsWith("/api/codex/logout") && c.method === "POST",
        ),
      ).toBe(true),
    );
  });
});
