/**
 * Component tests for the API-Keys section-health tab indicators.
 *
 * The tab bar shows a dot per tab: amber when the section still has to be set
 * up ("needs_setup"), red when it is set up but failing a live check
 * ("error"). "ok" / "unknown" stay silent. These tests pin that the dot and
 * its plain-language tooltip render from the /api/providers/section-health
 * rollup, that the red state drills down onto the failing provider, and that
 * sections the page no longer shows (the retired pipeline tiers) never light
 * a tab.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { ApiKeysView } from "@/views/ApiKeysView";

vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: "realtime",
    realtimeAvailable: true,
    statusKnown: true,
    sessionActive: false,
    activeSessionMode: null,
    connecting: false,
    lastStartError: null,
    activeModel: "",
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
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
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
    throw new Error(`unexpected fetch ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
}

function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ApiKeysView />
    </QueryClientProvider>,
  );
}

// An active, configured realtime provider — the shape the drill-down banner
// must land on when its live check fails.
const ACTIVE_REALTIME = {
  id: "gemini-live",
  label: "Gemini Live",
  tier: "realtime",
  auth_mode: "api_key",
  secret_keys: ["realtime_gemini_api_key"],
  secrets_set: { realtime_gemini_api_key: true },
  dashboard_url: null,
  login_cli: null,
  install_hint: null,
  credential_path_hint: null,
  configured: true,
  active: true,
  cli_installed: null,
  credential_help: null,
  signup_url: null,
  billing: "api",
  alt_credential: null,
  configuration_surface: "provider",
};

function health(sections: Record<string, unknown>) {
  return {
    sections: {
      brain: { status: "error", reason: "bad_key", detail: "OpenRouter: key invalid", subject_id: "openrouter" },
      stt: { status: "error", reason: "bad_key", detail: "Groq STT: key invalid", subject_id: "groq-api" },
      realtime: { status: "ok", reason: "ok", detail: "Gemini Live: ok", subject_id: "gemini-live" },
      subagents: { status: "ok", reason: "ok", detail: "", subject_id: null },
      advanced: { status: "unknown", reason: "unknown", detail: "", subject_id: null },
      ...sections,
    },
    checked_at: 0,
    cached: false,
  };
}

function routes(sectionHealth: unknown) {
  return {
    "/api/providers/section-health": () => ({ body: sectionHealth }),
    "/api/providers/gemini-live/realtime-options": () => ({
      body: { provider: "gemini-live", models: [], voices: [], current_model: "", current_voice: "", preview_available: false },
    }),
    "/api/providers": () => ({ body: { providers: [ACTIVE_REALTIME] } }),
    "/api/jarvis-agent/status": () => ({ body: { mapping: [], brain_primary: "" } }),
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ApiKeysView — section-health tab indicators", () => {
  it("marks a not-set-up tab amber ('Setup needed')", async () => {
    installFetchMock(
      routes(health({ subagents: { status: "needs_setup", reason: "not_configured", detail: "No agent", subject_id: null } })),
    );
    renderView();
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /-agents.*Setup needed/i })).toBeTruthy(),
    );
  });

  it("marks a broken tab red ('Not working') with the cause in the tooltip", async () => {
    installFetchMock(
      routes(health({ realtime: { status: "error", reason: "no_credits", detail: "Gemini Live: out of credit", subject_id: "gemini-live" } })),
    );
    renderView();
    const tab = await waitFor(() => screen.getByRole("tab", { name: /Realtime.*Not working/i }));
    expect(tab.getAttribute("title")).toMatch(/out of credit/i);
  });

  it("never lights a tab for the retired pipeline tiers", async () => {
    installFetchMock(routes(health({})));
    renderView();
    await waitFor(() => screen.getByTestId("realtime-provider-gemini-live"));
    // brain and stt are failing in the rollup, but the page shows neither.
    for (const tab of screen.getAllByRole("tab")) {
      expect(tab.getAttribute("title")).toBeNull();
    }
  });

  it("drills the error onto the failing provider with the cause in plain text", async () => {
    installFetchMock(
      routes(health({ realtime: { status: "error", reason: "rate_limited", detail: "Gemini Live: rate limited", subject_id: "gemini-live" } })),
    );
    renderView();
    const banner = await waitFor(() => screen.getByTestId("realtime-health-error-gemini-live"));
    expect(banner.textContent).toMatch(/Not working/i);
    expect(banner.textContent).toMatch(/rate limited/i);
  });

  it("does not mark the provider red when it is healthy", async () => {
    installFetchMock(routes(health({})));
    renderView();
    await waitFor(() => screen.getByTestId("realtime-provider-gemini-live"));
    expect(screen.queryByTestId("realtime-health-error-gemini-live")).toBeNull();
  });

  it("never attributes an obsolete failure of another provider to the active one", async () => {
    installFetchMock(
      routes(health({ realtime: { status: "error", reason: "timeout", detail: "OpenAI GPT-Live: timeout", subject_id: "openai-live" } })),
    );
    renderView();
    const tab = await waitFor(() => screen.getByRole("tab", { name: /^Realtime$/i }));
    expect(tab.getAttribute("title")).toBeNull();
    expect(screen.queryByTestId("realtime-health-error-gemini-live")).toBeNull();
  });
});
