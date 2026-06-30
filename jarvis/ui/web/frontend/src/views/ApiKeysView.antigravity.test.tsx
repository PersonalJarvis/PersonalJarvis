/**
 * Component tests for Antigravity in ApiKeysView.
 *
 * Antigravity is a Google-subscription worker for Subagents only. It must not
 * render as an activatable main Brain card; the Google login and active toggle
 * live in the Subagent section.
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
  return { fetchMock, calls };
}

function antigravityDescriptor(overrides: Record<string, unknown> = {}) {
  return {
    id: "antigravity",
    label: "Antigravity (Google subscription)",
    tier: "brain",
    auth_mode: "antigravity",
    secret_keys: [],
    secrets_set: {},
    dashboard_url: "https://antigravity.google",
    login_cli: ["agy"],
    install_hint: "Install Antigravity (agy) or sign in with the Gemini CLI",
    credential_path_hint: "~/.gemini/oauth_creds.json",
    configured: true, // connected -> configured mirrors it
    active: false,
    brain_switchable: false,
    cli_installed: true,
    antigravity_status: {
      installed: true,
      connected: true,
      mode: "oauth-personal",
      cli_kind: "agy",
      message: "Connected via your Google subscription.",
      version: "agy 0.1.0",
      user_email: "google-user@example.com",
      binary_path: "agy",
      error: null,
    },
    ...overrides,
  };
}

function antigravityNotConnected() {
  return antigravityDescriptor({
    configured: false,
    antigravity_status: {
      installed: true,
      connected: false,
      mode: "unknown",
      cli_kind: "agy",
      message: "Installed but not logged in — run the Google login.",
      version: "agy 0.1.0",
      user_email: null,
      binary_path: "agy",
      error: null,
    },
  });
}

function antigravityMissing() {
  return antigravityDescriptor({
    configured: false,
    cli_installed: false,
    antigravity_status: {
      installed: false,
      connected: false,
      mode: "unknown",
      cli_kind: null,
      message: "No Google CLI found.",
      version: null,
      user_email: null,
      binary_path: "",
      error: "no google cli binary",
    },
  });
}

const OPENCLAW_EMPTY = {
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
      jarvis: "antigravity",
      openclaw: "agy-cli (direct)",
      env_var: "Google-OAuth",
      env_fallback: null,
      key_set: true,
      is_active_brain: false,
    },
  ],
};

// The Telephony section mounts inside ApiKeysView, so stub its four endpoints.
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
    "/api/jarvis-agent/status": () => ({ body: OPENCLAW_EMPTY }),
    "/api/antigravity/status": () => ({
      body: provider.antigravity_status ?? {},
    }),
    "/api/jarvis-agent/switch": () => ({
      body: { ok: true, active: "antigravity", persisted: true },
    }),
    "/api/antigravity/login": () => ({
      body: { ok: true, pid: 123, message: "Google login was started" },
    }),
    "/api/antigravity/logout": () => ({
      body: { ok: true, message: "Antigravity (Google) was disconnected" },
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

describe("ApiKeysView — Antigravity (Google subscription) OAuth card", () => {
  it("renders the connected subscription card with email AND a Set-active radio", async () => {
    installFetchMock(routesFor(antigravityDescriptor()));
    render(<ApiKeysView />);
    // Antigravity lives in the "Subagents" category tab now; open it first.
    fireEvent.click(screen.getByRole("tab", { name: /jarvis-agents/i }));

    await waitFor(() =>
      expect(screen.getByText("Antigravity (Subscription)")).toBeTruthy(),
    );

    // The "Set active" control now lives ON the subscription card, so there is
    // no longer a separate "Antigravity (Google subscription)" provider card
    // below it (consolidated — one card per CLI that both connects and selects).
    expect(screen.queryByText("Antigravity (Google subscription)")).toBeNull();
    expect(screen.getByText(/google-user@example\.com/)).toBeTruthy();
    expect(screen.getByRole("radio")).toBeTruthy();
  });

  it("switches the subagent to Antigravity when connected", async () => {
    const { calls } = installFetchMock(routesFor(antigravityDescriptor()));
    render(<ApiKeysView />);
    // Antigravity lives in the "Subagents" category tab now; open it first.
    fireEvent.click(screen.getByRole("tab", { name: /jarvis-agents/i }));

    await waitFor(() => screen.getByRole("radio"));
    fireEvent.click(screen.getByRole("radio"));

    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.url.startsWith("/api/jarvis-agent/switch") &&
            c.method === "POST" &&
            (c.body as { provider?: string })?.provider === "antigravity",
        ),
      ).toBe(true),
    );
  });

  it("shows the Connect button while not logged in and starts login", async () => {
    const { calls } = installFetchMock(routesFor(antigravityNotConnected()));
    render(<ApiKeysView />);
    // Antigravity lives in the "Subagents" category tab now; open it first.
    fireEvent.click(screen.getByRole("tab", { name: /jarvis-agents/i }));

    await waitFor(() =>
      expect(screen.getByText("Connect")).toBeTruthy(),
    );

    fireEvent.click(screen.getByText("Connect"));
    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.url.startsWith("/api/antigravity/login") && c.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("disables the Connect button and shows the install hint when no CLI is installed", async () => {
    installFetchMock(routesFor(antigravityMissing()));
    render(<ApiKeysView />);
    // Antigravity lives in the "Subagents" category tab now; open it first.
    fireEvent.click(screen.getByRole("tab", { name: /jarvis-agents/i }));

    const connectBtn = await waitFor(() =>
      screen.getByText("Connect"),
    );
    expect((connectBtn.closest("button") as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText("Install Antigravity or the Gemini CLI before connecting."),
    ).toBeTruthy();
  });

  it("disconnects via POST /api/antigravity/logout", async () => {
    const { calls } = installFetchMock(routesFor(antigravityDescriptor()));
    render(<ApiKeysView />);
    // Antigravity lives in the "Subagents" category tab now; open it first.
    fireEvent.click(screen.getByRole("tab", { name: /jarvis-agents/i }));

    const disconnect = await waitFor(() => screen.getByText("Disconnect"));
    fireEvent.click(disconnect);

    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.url.startsWith("/api/antigravity/logout") && c.method === "POST",
        ),
      ).toBe(true),
    );
  });
});
