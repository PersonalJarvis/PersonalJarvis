/**
 * Component tests for the dedicated "Computer-Use" tab on the API-Keys
 * screen.
 *
 * Computer-Use (CU — the loop that reads the screen and clicks) used to be
 * hard-locked to the active Brain provider. This tab is an OVERLAY over the
 * SAME brain-provider cards (Claude/OpenAI/OpenRouter/Gemini) shown on the
 * Brain tab, but "Set active" here flips `computer_use_active` via
 * `POST /api/computer-use/switch` — a SEPARATE selection from `brain.primary`,
 * independent of the Brain tab's `active`. The CU provider is GLOBAL, so the
 * tab renders identically in Pipeline and Realtime mode.
 *
 * These tests pin: (1) the tab exists in both modes, (2) it renders the
 * brain-provider cards, (3) activating a card calls
 * `switchComputerUseProvider` with the BRAIN id (never a realtime id) and
 * flips `computer_use_active` without touching the Brain tab's `active` or
 * calling `/api/brain/switch`, (4) the CU-model picker renders under the tab,
 * keyed by the brain id.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

// ApiKeysView reads the live `[voice].mode` (for the Pipeline|Realtime mode
// switch) via useVoiceMode, which needs a QueryClientProvider — mocked here
// exactly like the other ApiKeysView.*.test.tsx files. realtimeAvailable=true
// so clicking the Realtime segment actually switches the tab set.
vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: "pipeline",
    realtimeAvailable: true,
    setMode: vi.fn(),
    isLoading: false,
    isSaving: false,
  }),
}));

// Stub CuModelSelector so this test asserts exactly which provider id the CU
// tab forwards to it, without pulling in its own model-catalog network calls
// (covered by CuModelSelector's own tests).
vi.mock("@/components/CuModelSelector", () => ({
  CuModelSelector: ({ providerId }: { providerId: string }) => (
    <div
      data-testid={`cu-model-selector-stub-${providerId}`}
      data-provider-id={providerId}
    />
  ),
}));

function brainProvider(overrides: Record<string, unknown> = {}) {
  return {
    id: "openai",
    label: "OpenAI",
    tier: "brain",
    auth_mode: "api_key",
    secret_keys: ["OPENAI_API_KEY"],
    secrets_set: { OPENAI_API_KEY: true },
    dashboard_url: null,
    login_cli: null,
    install_hint: null,
    credential_path_hint: null,
    configured: true,
    active: false,
    brain_switchable: true,
    cli_installed: null,
    credential_help: null,
    signup_url: null,
    billing: "api",
    recommended_model: null,
    alt_credential: null,
    computer_use_active: false,
    ...overrides,
  };
}

function realtimeProvider(overrides: Record<string, unknown> = {}) {
  return {
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
    ...overrides,
  };
}

let providersState: Array<Record<string, unknown>> = [];

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status >= 200 && status < 300 ? "OK" : "ERR",
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

function installFetchMock() {
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

    if (url.startsWith("/api/providers/section-health")) {
      return jsonResponse({ sections: {}, checked_at: 0, cached: false });
    }
    if (url === "/api/computer-use/switch" && method === "POST") {
      const providerId = (body as { provider?: string })?.provider ?? "";
      const spec = providersState.find((p) => p.id === providerId);
      if (!spec || !spec.configured) {
        return jsonResponse({ detail: "no key" }, 409);
      }
      providersState = providersState.map((p) => ({
        ...p,
        computer_use_active: p.tier === "brain" && p.id === providerId,
      }));
      return jsonResponse({
        ok: true,
        active: providerId,
        persisted: true,
        restart_required: false,
      });
    }
    if (url.startsWith("/api/providers")) {
      return jsonResponse({ providers: providersState });
    }
    throw new Error(`unexpected fetch ${url} (${method})`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return { calls };
}

import { ApiKeysView } from "@/views/ApiKeysView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ApiKeysView — Computer-Use tab", () => {
  it("appears in both Pipeline and Realtime mode", async () => {
    providersState = [brainProvider(), realtimeProvider()];
    installFetchMock();
    render(<ApiKeysView />);

    expect(await screen.findByRole("tab", { name: /tool model/i })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /^realtime/i }));

    expect(await screen.findByRole("tab", { name: /tool model/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /^realtime/i })).toBeTruthy();
  });

  it("renders the 4 brain-provider cards, same style as the Brain tab", async () => {
    providersState = [
      brainProvider({ id: "claude-api", label: "Claude" }),
      brainProvider({ id: "openai", label: "OpenAI", active: true }),
      brainProvider({ id: "openrouter", label: "OpenRouter" }),
      brainProvider({ id: "gemini", label: "Gemini", configured: false }),
    ];
    installFetchMock();
    render(<ApiKeysView />);

    fireEvent.click(await screen.findByRole("tab", { name: /tool model/i }));

    expect(await screen.findByText("Claude")).toBeTruthy();
    expect(screen.getByText("OpenAI")).toBeTruthy();
    expect(screen.getByText("OpenRouter")).toBeTruthy();
    expect(screen.getByText("Gemini")).toBeTruthy();
  });

  it("'Set active' calls switchComputerUseProvider with the brain id and flips computer_use_active independently of the Brain tab's active", async () => {
    providersState = [
      brainProvider({ id: "claude-api", label: "Claude", configured: true, active: false }),
      brainProvider({ id: "openai", label: "OpenAI", active: true, computer_use_active: true }),
    ];
    const { calls } = installFetchMock();
    render(<ApiKeysView />);

    fireEvent.click(await screen.findByRole("tab", { name: /tool model/i }));
    const claudeCard = (await screen.findByText("Claude")).closest("li") as HTMLElement | null;
    if (!claudeCard) throw new Error("Claude card not found");
    fireEvent.click(within(claudeCard).getByRole("radio"));

    // The backend call carries the BRAIN id, never a realtime id.
    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.url === "/api/computer-use/switch" &&
            c.method === "POST" &&
            (c.body as { provider?: string })?.provider === "claude-api",
        ),
      ).toBe(true),
    );

    // The CU tab's own radio flips to Claude after the refetch.
    await waitFor(() => {
      const card = screen.getByText("Claude").closest("li") as HTMLElement;
      expect(within(card).getByRole("radio")).toHaveProperty("checked", true);
    });

    // The Brain tab's real `active` (brain.primary) is untouched — still
    // OpenAI — and no `/api/brain/switch` call was ever made.
    fireEvent.click(screen.getByRole("tab", { name: /^brain$/i }));
    const brainOpenAiCard = screen.getByText("OpenAI").closest("li") as HTMLElement;
    expect(within(brainOpenAiCard).getByRole("radio")).toHaveProperty("checked", true);
    expect(calls.some((c) => c.url.startsWith("/api/brain/switch"))).toBe(false);
  });

  it("renders the CU-model picker under the Computer-Use tab, keyed by the brain id", async () => {
    providersState = [
      brainProvider({ id: "openai", label: "OpenAI", active: true, computer_use_active: true }),
    ];
    installFetchMock();
    render(<ApiKeysView />);

    fireEvent.click(await screen.findByRole("tab", { name: /tool model/i }));

    expect(await screen.findByTestId("cu-model-selector-stub-openai")).toBeTruthy();
  });
});
