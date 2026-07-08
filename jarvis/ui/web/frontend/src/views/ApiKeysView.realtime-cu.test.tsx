/**
 * Integration test for Feature B: the Computer-Use panel embedded in Realtime
 * mode on the API-Keys screen.
 *
 * Confirms the wiring end-to-end inside `ApiKeysView` — the `active ===
 * "realtime"` branch renders `RealtimeCategory`, which renders the existing
 * `ProviderCategory` (the realtime cards, unchanged) followed by
 * `RealtimeComputerUsePanel` — and that Pipeline tabs (brain/tts/stt) are
 * completely unaffected by the Realtime-only change (the plan's binding
 * constraint: Pipeline stays byte-for-byte unaffected).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

// ApiKeysView reads the live `[voice].mode` (for the Pipeline|Realtime mode
// switch) via useVoiceMode, which needs a QueryClientProvider — mocked here
// exactly like the other ApiKeysView.*.test.tsx files. realtimeAvailable=true
// so clicking the Realtime segment actually switches (mirrors
// ApiKeysView.two-mode.test.tsx).
vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: "pipeline",
    realtimeAvailable: true,
    setMode: vi.fn(),
    isLoading: false,
    isSaving: false,
  }),
}));

// Stub CuModelSelector so this test asserts exactly what
// RealtimeComputerUsePanel forwards to it, without pulling in its own model
// catalog network calls (covered by CuModelSelector's own tests).
vi.mock("@/components/CuModelSelector", () => ({
  CuModelSelector: ({ providerId }: { providerId: string }) => (
    <div data-testid="cu-model-selector-stub" data-provider-id={providerId} />
  ),
}));

const PROVIDERS = [
  {
    id: "openrouter",
    label: "OpenRouter",
    tier: "brain",
    auth_mode: "api_key",
    secret_keys: ["OPENROUTER_API_KEY"],
    secrets_set: { OPENROUTER_API_KEY: true },
    dashboard_url: null,
    login_cli: null,
    install_hint: null,
    credential_path_hint: null,
    configured: true,
    active: true,
    brain_switchable: true,
    cli_installed: null,
    credential_help: null,
    signup_url: null,
    billing: "api",
    recommended_model: "some-model",
    alt_credential: null,
  },
  {
    id: "openai-realtime",
    label: "OpenAI Realtime",
    tier: "realtime",
    auth_mode: "api_key",
    secret_keys: ["OPENAI_API_KEY"],
    secrets_set: { OPENAI_API_KEY: true },
    dashboard_url: null,
    login_cli: null,
    install_hint: null,
    credential_path_hint: null,
    configured: true,
    active: true,
    brain_switchable: true,
    cli_installed: null,
    credential_help: null,
    signup_url: null,
    billing: "api",
    alt_credential: null,
  },
];

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

function installFetchMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/providers/section-health")) {
      return jsonResponse({
        sections: {
          brain: { status: "ok", reason: "ok", detail: "" },
          tts: { status: "ok", reason: "ok", detail: "" },
          stt: { status: "ok", reason: "ok", detail: "" },
          realtime: { status: "ok", reason: "ok", detail: "" },
          subagents: { status: "unknown", reason: "unknown", detail: "" },
          advanced: { status: "unknown", reason: "unknown", detail: "" },
        },
        checked_at: 0,
        cached: false,
      });
    }
    if (url.startsWith("/api/providers")) {
      return jsonResponse({ providers: PROVIDERS });
    }
    if (url.startsWith("/api/openclaw/status")) {
      return jsonResponse({ mapping: [], brain_primary: "" });
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
}

import { ApiKeysView } from "@/views/ApiKeysView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ApiKeysView — Realtime Computer-Use panel (Feature B)", () => {
  it("shows the Computer-Use panel naming the active Brain provider once Realtime is selected", async () => {
    installFetchMock();
    render(<ApiKeysView />);

    // Pipeline tabs are the default view — no realtime panel yet.
    expect(screen.getByRole("tab", { name: /^Brain$/i })).toBeTruthy();
    expect(screen.queryByTestId("realtime-cu-panel")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /^realtime$/i }));

    const panel = await screen.findByTestId("realtime-cu-panel");
    expect(panel.textContent).toMatch(/OpenRouter/);

    // The BRAIN id ("openrouter"), never the realtime id ("openai-realtime").
    const stub = await screen.findByTestId("cu-model-selector-stub");
    expect(stub.getAttribute("data-provider-id")).toBe("openrouter");
  });

  it("switching back to Pipeline removes the Realtime Computer-Use panel, and Pipeline tabs are untouched", async () => {
    installFetchMock();
    render(<ApiKeysView />);
    fireEvent.click(screen.getByRole("button", { name: /^realtime$/i }));
    await screen.findByTestId("realtime-cu-panel");

    fireEvent.click(screen.getByRole("button", { name: /pipeline/i }));

    expect(screen.queryByTestId("realtime-cu-panel")).toBeNull();
    // The five Pipeline tabs render exactly as before Feature B.
    expect(screen.getByRole("tab", { name: /^Brain$/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /voice output/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /voice input/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /jarvis-agents/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /advanced/i })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /realtime/i })).toBeNull();
  });
});
