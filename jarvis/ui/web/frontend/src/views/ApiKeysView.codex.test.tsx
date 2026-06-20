/**
 * Component tests for the Codex provider card in ApiKeysView.
 *
 * Design (2026-06-08): Codex is a connection-only card (ChatGPT login → compact
 * "connected" badge, no OpenAI API-key field on the card). It IS selectable as
 * a brain like every other brain provider — but a chat brain needs an OpenAI API
 * key, which the ChatGPT login cannot back. So:
 *   - the Codex card shows a brain "activate" radio (parity with Gemini),
 *   - the radio is gated on `codex_brain_ready` (any OpenAI key present): with a
 *     key, clicking it switches the brain to Codex; without one, it warns
 *     honestly instead of a silent first-turn failure,
 *   - it still collapses to a compact connected badge once logged in.
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
    configured: true, // OAuth present -> is_credential_present() is true
    active: false,
    cli_installed: true,
    codex_brain_ready: false, // no OpenAI key -> cannot be a brain yet
    codex_status: {
      installed: true,
      connected: true,
      mode: "chatgpt",
      message: "Connected via ChatGPT (chatgpt-user@example.com).",
      version: "codex-cli 0.137.0",
      account_label: "ChatGPT/Codex-Login",
      user_email: "chatgpt-user@example.com",
      binary_path: "codex",
      error: null,
    },
    ...overrides,
  };
}

function codexDescriptorNotConnected() {
  return codexDescriptor({
    configured: false,
    codex_status: {
      installed: true,
      connected: false,
      mode: "unknown",
      message: "Codex is installed but not logged in — run 'codex login'.",
      version: "codex-cli 0.137.0",
      account_label: null,
      user_email: null,
      binary_path: "codex",
      error: null,
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
  mapping: [],
};

// The Telephony section now lives inside ApiKeysView (a tier section below the
// Subagent tier), so mounting the view fires the telephony panel's load(). Stub
// the four telephony endpoints with a graceful not-available/not-configured
// shape so the per-route fetch mock never throws on them.
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
    "/api/openclaw/status": () => ({ body: OPENCLAW_EMPTY }),
    "/api/brain/switch": () => ({ body: { ok: true, active: "codex", persisted: true } }),
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

describe("ApiKeysView — Codex is a selectable brain (parity with Gemini)", () => {
  it("renders a brain 'activate' selector on the Codex card", async () => {
    installFetchMock(routesFor(codexDescriptor()));
    render(<ApiKeysView />);

    await waitFor(() => expect(screen.getByText("OpenAI Codex")).toBeTruthy());

    // The card is selectable like every other brain provider.
    expect(screen.getByRole("radio")).toBeTruthy();
    // Compact connected badge for the ChatGPT login...
    expect(screen.getByTestId("codex-connected")).toBeTruthy();
    // No OpenAI key field on the card — Codex works as a brain over the ChatGPT
    // login (the OpenAI key, if any, lives on the separate "OpenAI" provider).
    expect(screen.queryByTestId("codex-brain-key")).toBeNull();
  });

  it("disables the brain radio (no error popup, no switch) when Codex has no OpenAI key", async () => {
    const { calls } = installFetchMock(routesFor(codexDescriptor())); // codex_brain_ready: false
    render(<ApiKeysView />);

    const radio = await waitFor(() => screen.getByRole("radio"));
    // Disabled until an OpenAI key is saved (the key field below unlocks it),
    // so we disable instead of firing a warning toast.
    expect((radio as HTMLInputElement).disabled).toBe(true);

    fireEvent.click(radio);
    expect(calls.some((c) => c.url.startsWith("/api/brain/switch"))).toBe(false);
  });

  it("switches the brain to Codex when an OpenAI key is present", async () => {
    const { calls } = installFetchMock(
      routesFor(codexDescriptor({ codex_brain_ready: true })),
    );
    render(<ApiKeysView />);

    await waitFor(() => screen.getByRole("radio"));
    fireEvent.click(screen.getByRole("radio"));

    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.url.startsWith("/api/brain/switch") &&
            c.method === "POST" &&
            (c.body as { provider?: string })?.provider === "codex",
        ),
      ).toBe(true),
    );
  });

  it("shows the connect button while not logged in (selector still present, greyed)", async () => {
    installFetchMock(routesFor(codexDescriptorNotConnected()));
    render(<ApiKeysView />);

    await waitFor(() => expect(screen.getByText("OpenAI Codex")).toBeTruthy());

    expect(screen.getByText("Connect with ChatGPT")).toBeTruthy();
    expect(screen.queryByTestId("codex-connected")).toBeNull();
    // Selector stays present for parity with the other brain providers.
    expect(screen.getByRole("radio")).toBeTruthy();
    // No OpenAI key field on the card (Codex is a brain over the ChatGPT login).
    expect(screen.queryByTestId("codex-brain-key")).toBeNull();
  });
});
