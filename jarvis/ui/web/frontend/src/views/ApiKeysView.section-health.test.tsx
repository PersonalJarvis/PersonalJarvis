/**
 * Component tests for the API-Keys section-health tab indicators.
 *
 * The segmented tab bar shows a corner dot per category: amber when the active
 * provider of that section still has to be set up ("needs_setup"), red when it
 * is set up but failing a live check ("error"). "ok" / "unknown" stay silent.
 * These tests pin that the dot + its plain-language tooltip render from the
 * /api/providers/section-health rollup.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { ApiKeysView } from "@/views/ApiKeysView";

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
    const status = (init?.method ?? "GET").toUpperCase();
    void status;
    const prefixes = Object.keys(routes).sort((a, b) => b.length - a.length);
    for (const prefix of prefixes) {
      if (url.startsWith(prefix)) {
        const { status: code = 200, body } = routes[prefix]();
        return {
          ok: code >= 200 && code < 300,
          status: code,
          statusText: code >= 200 && code < 300 ? "OK" : "ERR",
          json: async () => body,
          text: async () => JSON.stringify(body),
        } as Response;
      }
    }
    // SubagentSection fetches /api/{codex,antigravity,claude}/status behind a
    // .catch — an unmatched route there is harmless. Everything the tabs need is
    // mocked below.
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
}

const SECTION_HEALTH = {
  sections: {
    brain: { status: "needs_setup", reason: "not_configured", detail: "OpenRouter: no key set", subject_id: "openrouter" },
    "computer-use": { status: "ok", reason: "ok", detail: "OpenRouter: ok", subject_id: "openrouter" },
    tts: { status: "ok", reason: "ok", detail: "Gemini Flash: ok", subject_id: "gemini-flash-tts" },
    stt: { status: "error", reason: "bad_key", detail: "Groq STT: key invalid", subject_id: "groq-api" },
    realtime: { status: "unknown", reason: "unknown", detail: "", subject_id: null },
    subagents: { status: "unknown", reason: "unknown", detail: "", subject_id: null },
    advanced: { status: "unknown", reason: "unknown", detail: "", subject_id: null },
  },
  checked_at: 0,
  cached: false,
};

function baseRoutes(overrides: Record<string, () => RouteResult> = {}) {
  return {
    "/api/providers/section-health": () => ({ body: SECTION_HEALTH }),
    "/api/providers/openrouter/models": () => ({
      body: { provider: "openrouter", current_model: "", models: [], source: "static", fetched_at: 0, selects: "model" },
    }),
    "/api/providers/openrouter/cu-model": () => ({
      body: { provider: "openrouter", cu_model: "", effective_model: "auto", uses_main: true },
    }),
    "/api/providers": () => ({
      body: { providers: [BROKEN_BRAIN_PROVIDER, ACTIVE_TTS_PROVIDER, ACTIVE_STT_PROVIDER] },
    }),
    "/api/jarvis-agent/status": () => ({ body: { mapping: [], brain_primary: "" } }),
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// A single active, configured brain provider whose live check is failing — the
// shape the card-level indicator must drill the tab's red dot down onto.
const BROKEN_BRAIN_PROVIDER = {
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
  alt_credential: null,
};

const ACTIVE_TTS_PROVIDER = {
  ...BROKEN_BRAIN_PROVIDER,
  id: "gemini-flash-tts",
  label: "Gemini Flash TTS",
  tier: "tts",
};

const ACTIVE_STT_PROVIDER = {
  ...BROKEN_BRAIN_PROVIDER,
  id: "groq-api",
  label: "Groq STT",
  tier: "stt",
};

const SECTION_HEALTH_BRAIN_ERROR = {
  sections: {
    brain: { status: "error", reason: "rate_limited", detail: "OpenRouter: rate limited", subject_id: "openrouter" },
    "computer-use": { status: "ok", reason: "ok", detail: "OpenRouter: ok", subject_id: "openrouter" },
    tts: { status: "ok", reason: "ok", detail: "Gemini Flash: ok", subject_id: "gemini-flash-tts" },
    stt: { status: "ok", reason: "ok", detail: "faster-whisper: local", subject_id: "groq-api" },
    realtime: { status: "unknown", reason: "unknown", detail: "", subject_id: null },
    subagents: { status: "unknown", reason: "unknown", detail: "", subject_id: null },
    advanced: { status: "unknown", reason: "unknown", detail: "", subject_id: null },
  },
  checked_at: 0,
  cached: false,
};

// Routes for a fully-rendered brain card: the active card mounts the model + CU
// pickers, which fetch their own catalogs on mount.
function cardRoutes(sectionHealth: unknown = SECTION_HEALTH_BRAIN_ERROR) {
  return baseRoutes({
    "/api/providers/section-health": () => ({ body: sectionHealth }),
    "/api/providers/openrouter/models": () => ({
      body: { provider: "openrouter", current_model: "", models: [], source: "static", fetched_at: 0, selects: "model" },
    }),
    "/api/providers/openrouter/cu-model": () => ({
      body: { provider: "openrouter", cu_model: "", effective_model: "auto", uses_main: true },
    }),
    "/api/providers": () => ({ body: { providers: [BROKEN_BRAIN_PROVIDER] } }),
  });
}

describe("ApiKeysView — section-health tab indicators", () => {
  it("marks a not-set-up tab amber ('Setup needed')", async () => {
    installFetchMock(baseRoutes());
    render(<ApiKeysView />);
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /Brain.*Setup needed/i })).toBeTruthy(),
    );
  });

  it("marks a broken tab red ('Not working') with the cause in the tooltip", async () => {
    installFetchMock(baseRoutes());
    render(<ApiKeysView />);
    const sttTab = await waitFor(() =>
      screen.getByRole("tab", { name: /Voice Input.*Not working/i }),
    );
    expect(sttTab.getAttribute("title")).toMatch(/key invalid/i);
  });

  it("leaves a healthy tab without any indicator", async () => {
    installFetchMock(baseRoutes());
    render(<ApiKeysView />);
    // The "Voice Output" tab is ok → no "Setup needed" / "Not working" in its name.
    await waitFor(() => screen.getByRole("tab", { name: /Brain/i }));
    const ttsTab = screen.getByRole("tab", { name: /^Voice Output$/i });
    expect(ttsTab.getAttribute("title")).toBeNull();
  });

  it("drills the error onto the exact failing card with the cause in plain text", async () => {
    installFetchMock(cardRoutes());
    render(<ApiKeysView />);
    // The active OpenRouter card surfaces an inline error banner naming the cause,
    // so the user sees WHICH provider broke and WHY — not just the tab's red dot.
    const banner = await waitFor(() =>
      screen.getByTestId("provider-health-error-openrouter"),
    );
    expect(banner.textContent).toMatch(/Not working/i);
    expect(banner.textContent).toMatch(/rate limited/i);
  });

  it("does not mark a card red when its provider is healthy", async () => {
    installFetchMock(
      baseRoutes({
        "/api/providers/section-health": () => ({ body: SECTION_HEALTH }),
        "/api/providers/openrouter/models": () => ({
          body: { provider: "openrouter", current_model: "", models: [], source: "static", fetched_at: 0, selects: "model" },
        }),
        "/api/providers/openrouter/cu-model": () => ({
          body: { provider: "openrouter", cu_model: "", effective_model: "auto", uses_main: true },
        }),
        // brain is "needs_setup" in SECTION_HEALTH, never "error" → no card banner.
        "/api/providers": () => ({ body: { providers: [BROKEN_BRAIN_PROVIDER] } }),
      }),
    );
    render(<ApiKeysView />);
    await waitFor(() => screen.getByRole("tab", { name: /Brain/i }));
    expect(screen.queryByTestId("provider-health-error-openrouter")).toBeNull();
  });

  it("never attributes an obsolete NVIDIA timeout to active OpenRouter", async () => {
    installFetchMock(
      cardRoutes({
        ...SECTION_HEALTH_BRAIN_ERROR,
        sections: {
          ...SECTION_HEALTH_BRAIN_ERROR.sections,
          brain: {
            status: "error",
            reason: "timeout",
            detail: "NVIDIA NIM: timeout after 60.0s",
            subject_id: "nvidia",
          },
        },
      }),
    );
    render(<ApiKeysView />);

    const brainTab = await waitFor(() => screen.getByRole("tab", { name: /^Brain$/i }));
    expect(brainTab.getAttribute("title")).toBeNull();
    expect(screen.queryByTestId("provider-health-error-openrouter")).toBeNull();
  });
});
