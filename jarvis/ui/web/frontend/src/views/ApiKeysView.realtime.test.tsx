/**
 * The API Keys page is realtime-only: no Pipeline|Realtime switch, no
 * pipeline tiers, no Local Mode toggle. These tests pin the tab set, the
 * provider list (one click on a provider with a saved key makes it the voice),
 * the experimental acknowledgement, and the way back onto Realtime for an
 * install still pinned to the retired pipeline engine.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const setVoiceMode = vi.fn();
let mockProviders: unknown[] = [];
let mockHealth: Record<string, unknown> = {};
let voiceState: Record<string, unknown> = {};

vi.mock("@/hooks/useProviders", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/hooks/useProviders")>()),
  useProviders: () => ({
    providers: mockProviders,
    loading: false,
    error: null,
    refetch: vi.fn(),
    setActiveOptimistic: vi.fn(),
  }),
  useSectionHealth: () => ({ health: mockHealth }),
}));

vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: "realtime",
    realtimeAvailable: true,
    statusKnown: true,
    requiresWebRtcOffer: false,
    transportOfferReady: null,
    transportOfferDetail: "",
    transportIssue: null,
    activeProvider: "openai-live",
    activeProviderLabel: "OpenAI GPT-Live",
    activeModel: "gpt-live-1",
    sessionActive: false,
    activeSessionMode: null,
    connecting: false,
    lastStartError: null,
    setMode: setVoiceMode,
    isLoading: false,
    isSaving: false,
    ...voiceState,
  }),
}));

import { ApiKeysView } from "@/views/ApiKeysView";
import { clearApiKeysTabRequest, requestApiKeysTab } from "@/lib/apiKeysTab";

const BASE = {
  tier: "realtime",
  auth_mode: "api_key",
  dashboard_url: null,
  login_cli: null,
  install_hint: null,
  credential_path_hint: null,
  cli_installed: null,
  credential_help: null,
  signup_url: null,
  billing: "api",
  alt_credential: null,
  configuration_surface: "provider",
};

const LIVE = {
  ...BASE,
  id: "openai-live",
  label: "OpenAI GPT-Live",
  configuration_surface: "live",
  secret_keys: ["openai_api_key"],
  secrets_set: { openai_api_key: true },
  configured: true,
  active: true,
};

const GEMINI = {
  ...BASE,
  id: "gemini-live",
  label: "Gemini Live",
  secret_keys: ["realtime_gemini_api_key"],
  secrets_set: { realtime_gemini_api_key: true },
  configured: true,
  active: false,
};

const VERTEX = {
  ...BASE,
  id: "vertex-live",
  label: "Vertex AI Live",
  secret_keys: ["realtime_vertex_api_key"],
  secrets_set: { realtime_vertex_api_key: false },
  configured: false,
  active: false,
};

const LOCAL = {
  ...BASE,
  id: "local-realtime",
  label: "Local voice",
  auth_mode: "none",
  billing: "local",
  secret_keys: [],
  secrets_set: {},
  configured: true,
  active: false,
  experimental: true,
};

const PIPELINE_BRAIN = {
  ...BASE,
  id: "openrouter",
  label: "OpenRouter",
  tier: "brain",
  secret_keys: ["OPENROUTER_API_KEY"],
  secrets_set: { OPENROUTER_API_KEY: true },
  configured: true,
  active: true,
};

const LIVE_PROFILE = {
  key_ready: true,
  active: true,
  agent_configured: true,
  profile: {
    model: "gpt-live-1",
    voice: "gleam",
    backend_model: "gpt-6-luna",
    reasoning_effort: "medium",
    web_search: true,
    instructions: "",
    backend_instructions: "",
    configured: true,
  },
};

const requests: { url: string; method: string; body: unknown }[] = [];

function installFetch() {
  requests.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      requests.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
      const body = url.startsWith("/api/live/profile")
        ? LIVE_PROFILE
        : url.startsWith("/api/live/options")
          ? { models: [{ id: "gpt-6-luna", label: "gpt-6-luna" }], voices: ["gleam"], efforts: ["", "medium"] }
          : url.includes("/realtime-options")
            ? { provider: "x", models: [], voices: [], current_model: "", current_voice: "", preview_available: false }
            : url.startsWith("/api/realtime/switch")
              ? { active: "x", restart_required: false }
              : url.startsWith("/api/jarvis-agent/status")
                ? { mapping: [], brain_primary: "" }
                : {};
      return {
        ok: true,
        status: 200,
        json: async () => body,
        text: async () => JSON.stringify(body),
      } as Response;
    }),
  );
}

function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ApiKeysView />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockProviders = [LIVE, GEMINI, VERTEX, LOCAL, PIPELINE_BRAIN];
  mockHealth = {};
  voiceState = {};
  installFetch();
  try {
    window.localStorage.clear();
  } catch {
    // jsdom always has storage; nothing to clear otherwise.
  }
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  clearApiKeysTabRequest();
});

describe("ApiKeysView — realtime only", () => {
  it("offers exactly the realtime, agents, key and advanced tabs", () => {
    renderView();
    const names = screen.getAllByRole("tab").map((tab) => tab.textContent);
    expect(names).toHaveLength(4);
    expect(screen.getByRole("tab", { name: /^realtime$/i }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: /-agents$/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /key$/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /^advanced$/i })).toBeTruthy();
    for (const gone of [/^brain$/i, /voice input/i, /voice output/i, /wording/i, /tool model/i]) {
      expect(screen.queryByRole("tab", { name: gone })).toBeNull();
    }
  });

  it("has no Pipeline switch and no Local Mode toggle", () => {
    renderView();
    expect(screen.queryByTestId("voice-engine-header-control")).toBeNull();
    expect(screen.queryByTestId("local-mode-switch")).toBeNull();
    expect(screen.queryByText(/pipeline/i)).toBeNull();
  });

  it("lists only realtime providers, with the active one marked", () => {
    renderView();
    const list = screen.getByTestId("realtime-provider-list");
    expect(within(list).getAllByRole("button")).toHaveLength(5);
    expect(screen.getByTestId("realtime-provider-openai-live-subscription").textContent).toContain("GPT Subscription");
    expect(screen.queryByTestId("realtime-provider-openrouter")).toBeNull();
    expect(screen.getByTestId("realtime-provider-openai-live").dataset.active).toBe("true");
    expect(screen.getByTestId("realtime-provider-gemini-live").dataset.active).toBe("false");
  });

  it("says what the voice runs on right now", () => {
    renderView();
    const card = screen.getByTestId("voice-now");
    expect(card.textContent).toContain("OpenAI GPT-Live");
    expect(card.textContent).toContain("gpt-live-1");
    expect(screen.getByTestId("voice-now-state").textContent).toMatch(/ready for your next call/i);
  });

  it("switches the voice with one click on a provider whose key is saved", async () => {
    renderView();
    fireEvent.click(screen.getByTestId("realtime-provider-gemini-live"));
    await waitFor(() =>
      expect(requests.some((r) => r.url === "/api/realtime/switch")).toBe(true),
    );
    const call = requests.find((r) => r.url === "/api/realtime/switch");
    expect(call?.body).toMatchObject({ provider: "gemini-live", persist: true, accept_experimental: false });
    // The voice already runs on Realtime, so the engine is left alone.
    expect(setVoiceMode).not.toHaveBeenCalled();
  });

  it("only opens the settings of a provider that has no key yet", () => {
    renderView();
    fireEvent.click(screen.getByTestId("realtime-provider-vertex-live"));
    expect(screen.getByTestId("realtime-settings-vertex-live")).toBeTruthy();
    expect(screen.getByTestId("realtime-provider-vertex-live").getAttribute("aria-pressed")).toBe("true");
    expect(requests.some((r) => r.url === "/api/realtime/switch")).toBe(false);
  });

  it("asks once before an experimental provider becomes the voice", async () => {
    renderView();
    fireEvent.click(screen.getByTestId("realtime-provider-local-realtime"));
    const dialog = await screen.findByTestId("realtime-consent-dialog");
    expect(requests.some((r) => r.url === "/api/realtime/switch")).toBe(false);
    fireEvent.click(within(dialog).getByRole("button", { name: /yes/i }));
    await waitFor(() =>
      expect(requests.find((r) => r.url === "/api/realtime/switch")?.body).toMatchObject({
        provider: "local-realtime",
        accept_experimental: true,
      }),
    );
    expect(window.localStorage.getItem("jarvis.experimentalConsent.local-realtime")).toBe("1");
  });

  it("re-selects GPT-Live by saving its stored profile", async () => {
    mockProviders = [
      { ...LIVE, active: false },
      { ...GEMINI, active: true },
      VERTEX,
      LOCAL,
    ];
    renderView();
    // The profile has to have arrived before a click can switch to it.
    await waitFor(() => expect(requests.some((r) => r.url === "/api/live/profile")).toBe(true));
    await act(async () => {
      await Promise.resolve();
    });
    fireEvent.click(screen.getByTestId("realtime-provider-openai-live"));
    await waitFor(() =>
      expect(requests.some((r) => r.url === "/api/live/profile" && r.method === "PUT")).toBe(true),
    );
    const put = requests.find((r) => r.url === "/api/live/profile" && r.method === "PUT");
    expect(put?.body).toMatchObject({ backend_model: "gpt-6-luna", configured: true });
  });
});

describe("ApiKeysView — an install still on the pipeline engine", () => {
  it("offers one click back onto Realtime", () => {
    voiceState = { mode: "pipeline" };
    renderView();
    expect(screen.getByTestId("voice-now").textContent).toMatch(/realtime is off/i);
    fireEvent.click(screen.getByTestId("voice-now-turn-on"));
    expect(setVoiceMode).toHaveBeenCalledWith("realtime");
  });

  it("treats a click on the provider already in place as 'use it'", () => {
    voiceState = { mode: "pipeline" };
    renderView();
    fireEvent.click(screen.getByTestId("realtime-provider-openai-live"));
    expect(setVoiceMode).toHaveBeenCalledWith("realtime");
    expect(requests.some((r) => r.method === "PUT" || r.url === "/api/realtime/switch")).toBe(false);
  });

  it("moves the engine to Realtime when a provider is picked", async () => {
    voiceState = { mode: "pipeline" };
    renderView();
    fireEvent.click(screen.getByTestId("realtime-provider-gemini-live"));
    await waitFor(() => expect(setVoiceMode).toHaveBeenCalledWith("realtime"));
  });

  it("explains a call that fell back off Realtime", () => {
    voiceState = { sessionActive: true, activeSessionMode: "pipeline" };
    renderView();
    expect(screen.getByTestId("voice-now-state").textContent).toMatch(/backup voice path/i);
    expect(screen.getByTestId("voice-now-note-backup")).toBeTruthy();
  });
});

describe("ApiKeysView — tab requests", () => {
  it("opens the tab the first-run guide asks for", () => {
    requestApiKeysTab("subagents");
    renderView();
    expect(screen.getByRole("tab", { name: /-agents$/i }).getAttribute("aria-selected")).toBe("true");
  });

  it("falls back to Realtime for a retired pipeline tab", () => {
    requestApiKeysTab("brain");
    renderView();
    expect(screen.getByRole("tab", { name: /^realtime$/i }).getAttribute("aria-selected")).toBe("true");
  });
});
