import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ProviderDescriptor } from "@/hooks/useProviders";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

const providersState = vi.hoisted(() => ({
  providers: [] as unknown[],
  loading: false,
  error: null as string | null,
  refetch: vi.fn(),
  // Mirrors the real helper's wire shape so the local-path test can assert
  // the request the backend would see.
  switchBrainProvider: vi.fn(async (provider: string) => {
    await fetch("/api/brain/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
  }),
}));
vi.mock("@/hooks/useProviders", () => ({
  useProviders: () => ({
    providers: providersState.providers,
    loading: providersState.loading,
    error: providersState.error,
    refetch: providersState.refetch,
  }),
  switchBrainProvider: providersState.switchBrainProvider,
}));
// The real form is exercised by its own tests; here it is a probe that
// reports which slot it was mounted for and lets a test "save" a key.
vi.mock("@/components/ApiKeyForm", () => ({
  ApiKeyForm: (p: { secretKey: string; onSavedActivate?: () => void }) => (
    <div data-testid={`form-${p.secretKey}`}>
      <button onClick={() => p.onSavedActivate?.()}>saved</button>
    </div>
  ),
}));

import { ApiKeysStep, startableProviders } from "./ApiKeysStep";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  providersState.providers = [];
  providersState.loading = false;
  providersState.error = null;
  providersState.refetch.mockClear();
  providersState.switchBrainProvider.mockClear();
});

function provider(overrides: Partial<ProviderDescriptor>): ProviderDescriptor {
  return {
    id: "x",
    label: "X",
    tier: "brain",
    auth_mode: "api_key",
    secret_keys: ["X_API_KEY"],
    secrets_set: { X_API_KEY: false },
    dashboard_url: "https://x.example/keys",
    login_cli: null,
    install_hint: null,
    credential_path_hint: null,
    configured: false,
    active: false,
    cli_installed: null,
    credential_help: null,
    signup_url: null,
    billing: "api",
    ...overrides,
  } as ProviderDescriptor;
}

type ProbeShape = { source: string; models: { id: string }[] } | null;

/** Stub fetch for the local-path probe (+ the brain switch and the engine pin). */
function stubFetch(probe: ProbeShape) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/providers/ollama/models") {
        if (probe === null) throw new Error("network down");
        return { ok: true, json: async () => probe } as Response;
      }
      if (url === "/api/brain/switch" || url === "/api/settings/voice-mode") {
        return { ok: true, json: async () => ({ ok: true }) } as Response;
      }
      throw new Error(`unexpected fetch: ${url}`);
    }),
  );
  return calls;
}

function renderStep(overrides: Record<string, unknown> = {}) {
  const props = {
    onb: {} as never,
    goNext: vi.fn(),
    goBack: vi.fn(),
    skip: vi.fn(),
    isFirst: false,
    isLast: false,
    setSummary: vi.fn(),
    summaries: {},
    ...overrides,
  };
  render(<ApiKeysStep {...props} />);
  return props;
}

it("startableProviders keeps key-based brain providers, recommended first", () => {
  const list = startableProviders([
    provider({ id: "b", label: "B" }),
    provider({ id: "tts", tier: "tts" }),
    provider({ id: "cli", auth_mode: "codex" }),
    provider({ id: "a", label: "A", recommended: true }),
    provider({ id: "nokey", secret_keys: [] }),
  ]);
  expect(list.map((p) => p.id)).toEqual(["a", "b"]);
});

it("renders the catalog as a register, opens the first row's form, and lets a saved key continue", async () => {
  providersState.providers = [
    provider({ id: "openrouter", label: "OpenRouter", recommended: true, secret_keys: ["OPENROUTER_API_KEY"], secrets_set: { OPENROUTER_API_KEY: false } }),
    provider({ id: "b", label: "B" }),
  ];
  stubFetch(null);
  const props = renderStep();

  expect(screen.getByTestId("onboarding-provider-openrouter")).toBeDefined();
  expect(screen.getByTestId("form-OPENROUTER_API_KEY")).toBeDefined();
  expect(screen.queryByTestId("form-X_API_KEY")).toBeNull();
  const next = screen.getByTestId("onboarding-primary") as HTMLButtonElement;
  expect(next.disabled).toBe(true);
  expect(screen.getByTestId("onboarding-keys-later")).toBeDefined();

  // Saving through the form makes that provider the active brain when none is.
  fireEvent.click(screen.getByText("saved"));
  await waitFor(() =>
    expect(providersState.switchBrainProvider).toHaveBeenCalledWith("openrouter"),
  );

  // The catalog refresh now reports the key — Continue opens up.
  providersState.providers = [
    provider({ id: "openrouter", label: "OpenRouter", recommended: true, secret_keys: ["OPENROUTER_API_KEY"], secrets_set: { OPENROUTER_API_KEY: true }, configured: true }),
  ];
  cleanup();
  const again = renderStep();
  expect((screen.getByTestId("onboarding-primary") as HTMLButtonElement).disabled).toBe(false);
  expect(again.setSummary).toHaveBeenLastCalledWith("OpenRouter");
  expect(props.skip).not.toHaveBeenCalled();
});

it("'later' skips honestly, and the summary stays empty", async () => {
  providersState.providers = [provider({ id: "b", label: "B" })];
  stubFetch(null);
  const props = renderStep();
  await screen.findByText("onboarding.api_keys.local_missing");
  fireEvent.click(screen.getByTestId("onboarding-keys-later"));
  expect(props.skip).toHaveBeenCalledTimes(1);
  expect(props.setSummary).toHaveBeenLastCalledWith(null);
});

it("a failed catalog load says so and still offers the local path", async () => {
  providersState.error = "HTTP 503";
  stubFetch(null);
  renderStep();
  expect(screen.getByText("onboarding.api_keys.load_failed")).toBeDefined();
  await screen.findByText("onboarding.api_keys.local_missing");
});

it("probes through the backend, activates local, pins pipeline, and unlocks Continue", async () => {
  const calls = stubFetch({ source: "live", models: [{ id: "qwen3.5:9b" }] });
  const props = renderStep();

  await screen.findByText("onboarding.api_keys.local_detected");
  expect((screen.getByTestId("onboarding-primary") as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "onboarding.api_keys.local_use_button" }));

  await screen.findByTestId("local-active");
  const switchCall = calls.find((c) => c.url === "/api/brain/switch");
  expect(JSON.parse(String(switchCall?.init?.body))).toMatchObject({ provider: "ollama" });
  const modeCall = calls.find((c) => c.url === "/api/settings/voice-mode");
  expect(modeCall?.init?.method).toBe("PUT");
  expect(JSON.parse(String(modeCall?.init?.body))).toMatchObject({ mode: "pipeline" });
  // The probe went through the backend catalog route — never a
  // browser-direct localhost:11434 call.
  expect(calls[0].url).toBe("/api/providers/ollama/models");
  expect(window.localStorage.getItem("jarvis.providers.localMode")).toBe("1");

  expect((screen.getByTestId("onboarding-primary") as HTMLButtonElement).disabled).toBe(false);
  expect(props.setSummary).toHaveBeenLastCalledWith("onboarding.api_keys.summary_local");
});

it("with Ollama running but empty, explains the missing model and shows no activate button", async () => {
  stubFetch({ source: "live", models: [] });
  renderStep();
  await screen.findByText("onboarding.api_keys.local_detected_empty");
  expect(screen.queryByRole("button", { name: "onboarding.api_keys.local_use_button" })).toBeNull();
});
