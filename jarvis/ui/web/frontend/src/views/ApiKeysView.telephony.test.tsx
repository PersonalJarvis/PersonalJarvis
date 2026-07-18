/**
 * Component tests for the Telephony tier section embedded in ApiKeysView.
 *
 * Design (2026-06-09): the former standalone "Telephony" sidebar screen was
 * folded into the API-Keys view as another tier section (header + the existing
 * status/credentials/scripts/calls cards), shown after the Subagent tier. These
 * tests pin that the section header renders and that the embedded TelephonyPanel
 * loads and shows live telephony data from /api/telephony/*.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiKeysView } from "@/views/ApiKeysView";
import { useEventStore } from "@/store/events";

// ApiKeysView now reads the live `[voice].mode` (for the Pipeline|Realtime
// mode switch's "Active" badge only) via useVoiceMode, which needs a
// QueryClientProvider. These tests render ApiKeysView standalone, so the
// hook is mocked — the mode switch itself is exercised by
// ApiKeysView.two-mode.test.tsx.
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
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
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
    throw new Error(`unexpected fetch ${method} ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  Object.assign(navigator, {
    clipboard: { writeText: vi.fn(async () => undefined) },
  });
}

const GEMINI_BRAIN = {
  id: "gemini",
  label: "Google Gemini",
  tier: "brain",
  auth_mode: "api_key",
  secret_keys: ["gemini_api_key"],
  secrets_set: { gemini_api_key: true },
  dashboard_url: "https://aistudio.google.com/apikey",
  install_hint: null,
  credential_path_hint: null,
  configured: true,
  active: true,
};

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
  mapping: [],
};

const TELEPHONY_STATUS_LIVE = {
  available: true,
  configured: true,
  enabled: true,
  account_sid_masked: "AC••••••cdef",
  phone_number: "+4930123456789",
  public_base_url: "https://jarvis.example.com",
  webhook_url: "https://jarvis.example.com/api/telephony/voice",
  auth_token_set: true,
  twilio_reachable: true,
  twilio_error: null,
  tts_provider: "gemini-flash-tts",
  tts_voice: "Charon",
  active_calls: 0,
  max_call_seconds: 600,
};

const TELEPHONY_CONFIG_LIVE = {
  enabled: true,
  account_sid: "AC0123456789abcdef0123456789abcdef",
  phone_number: "+4930123456789",
  public_base_url: "https://jarvis.example.com",
  greeting: "",
  language_code: "de-DE",
  fallback_mode: "media",
  max_call_seconds: 600,
  auth_token_set: true,
};

function routes(): Record<string, () => RouteResult> {
  return {
    "/api/providers": () => ({ body: { providers: [GEMINI_BRAIN] } }),
    "/api/jarvis-agent/status": () => ({ body: JARVIS_AGENT_EMPTY }),
    "/api/telephony/status": () => ({ body: TELEPHONY_STATUS_LIVE }),
    "/api/telephony/config": () => ({ body: TELEPHONY_CONFIG_LIVE }),
    "/api/telephony/scripts": () => ({ body: { scripts: [] } }),
    "/api/telephony/calls": () => ({ body: { calls: [] } }),
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ApiKeysView — embedded Telephony tier", () => {
  it("renders the Telephony tier header inside the API-Keys view", async () => {
    installFetchMock(routes());
    render(<ApiKeysView />);
    // Telephony now lives in the de-emphasized "Advanced" tab; open it first.
    fireEvent.click(screen.getByRole("tab", { name: /advanced/i }));

    // English is the test-default locale; tier_telephony => "Telephony".
    await waitFor(() => expect(screen.getByText("Telephony")).toBeTruthy());
  });

  it("loads live telephony status (Charon voice) from /api/telephony", async () => {
    installFetchMock(routes());
    render(<ApiKeysView />);
    // Telephony now lives in the de-emphasized "Advanced" tab; open it first.
    fireEvent.click(screen.getByRole("tab", { name: /advanced/i }));

    await waitFor(() => {
      expect(screen.getByTestId("status-tts-voice").textContent).toBe("Charon");
    });
    expect(screen.getByTestId("status-phone-number").textContent).toBe(
      "+4930123456789",
    );
  });

  it("offers a 'Setup script' button that navigates to the telephony-setup page", async () => {
    installFetchMock(routes());
    render(<ApiKeysView />);
    // Telephony now lives in the de-emphasized "Advanced" tab; open it first.
    fireEvent.click(screen.getByRole("tab", { name: /advanced/i }));

    const btn = await waitFor(() =>
      screen.getByRole("button", { name: /Setup script/i }),
    );
    fireEvent.click(btn);
    expect(useEventStore.getState().activeSection).toBe("telephony-setup");
  });
});
