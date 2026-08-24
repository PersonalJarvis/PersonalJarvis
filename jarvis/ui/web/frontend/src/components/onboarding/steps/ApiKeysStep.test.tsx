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
// Starter plans: the catalog a test hands in, plus spies on select/apply.
const plansState = vi.hoisted(() => ({
  plans: [] as unknown[],
  selected: null as string | null,
  fail: false,
  selectStarterPlan: vi.fn(async () => undefined),
  applyStarterPlan: vi.fn(async () => ({ applied: ["brain"], failed: [], modeSet: true })),
}));
vi.mock("@/hooks/useStarterPlans", () => ({
  getStarterPlans: async () => {
    if (plansState.fail) throw new Error("HTTP 404");
    return { plans: plansState.plans, selected: plansState.selected, custom_id: "custom" };
  },
  selectStarterPlan: plansState.selectStarterPlan,
  applyStarterPlan: plansState.applyStarterPlan,
}));
vi.mock("@/components/ReadyCelebration", () => ({
  ReadyCelebration: () => <div data-testid="ready-celebration-stub" />,
}));

import { ApiKeysStep, planKeysComplete, providersForPlan, startableProviders } from "./ApiKeysStep";

const GEMINI_PLAN = {
  id: "gemini-pipeline",
  label: "Pipeline with Gemini",
  summary: "One key.",
  mode: "pipeline" as const,
  recommended: true,
  assignments: { brain: "gemini", tts: "gemini-flash-tts" },
  key_slots: [{ family: "gemini", slot: "gemini_api_key", label: "Google Gemini", present: false }],
  keys_complete: false,
  ready_sections: ["brain", "computer-use", "tts", "stt"],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  providersState.providers = [];
  providersState.loading = false;
  providersState.error = null;
  providersState.refetch.mockClear();
  providersState.switchBrainProvider.mockClear();
  plansState.plans = [];
  plansState.selected = null;
  plansState.fail = true;
  plansState.selectStarterPlan.mockClear();
  plansState.applyStarterPlan.mockClear();
});

// The legacy tests below describe the "custom" path: no plan catalog.
plansState.fail = true;

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

it("providersForPlan keeps only the cards whose primary slot the plan needs", () => {
  const gemini = provider({ id: "gemini", label: "Google Gemini", secret_keys: ["gemini_api_key"], secrets_set: { gemini_api_key: false } });
  const other = provider({ id: "b", label: "B" });
  expect(providersForPlan(GEMINI_PLAN, [gemini, other]).map((p) => p.id)).toEqual(["gemini"]);
  expect(providersForPlan(null, [gemini, other]).map((p) => p.id)).toEqual(["gemini", "b"]);
  expect(planKeysComplete(GEMINI_PLAN, [gemini, other])).toBe(false);
  const saved = { ...gemini, secrets_set: { gemini_api_key: true } };
  expect(planKeysComplete(GEMINI_PLAN, [saved, other])).toBe(true);
});

it("preselects the recommended plan, filters the list to its key, and applies the plan once the key is in", async () => {
  plansState.fail = false;
  plansState.plans = [GEMINI_PLAN];
  providersState.providers = [
    provider({ id: "gemini", label: "Google Gemini", secret_keys: ["gemini_api_key"], secrets_set: { gemini_api_key: false } }),
    provider({ id: "b", label: "B" }),
  ];
  stubFetch(null);
  renderStep();

  const row = await screen.findByTestId("onboarding-plan-gemini-pipeline");
  expect(row.getAttribute("aria-checked")).toBe("true");
  await waitFor(() => expect(screen.getByTestId("form-gemini_api_key")).toBeDefined());
  expect(screen.queryByTestId("form-X_API_KEY")).toBeNull();
  expect(plansState.applyStarterPlan).not.toHaveBeenCalled();

  // The catalog refresh reports the key — the plan is applied without any
  // further click, and the brain auto-switch stays out of the plan's way.
  providersState.providers = [
    provider({ id: "gemini", label: "Google Gemini", secret_keys: ["gemini_api_key"], secrets_set: { gemini_api_key: true }, configured: true }),
  ];
  cleanup();
  const props = renderStep();
  await screen.findByTestId("onboarding-plan-applied");
  expect(plansState.applyStarterPlan).toHaveBeenCalledTimes(1);
  expect(plansState.selectStarterPlan).toHaveBeenCalledWith("gemini-pipeline");
  expect(screen.getByTestId("ready-celebration-stub")).toBeDefined();
  expect((screen.getByTestId("onboarding-primary") as HTMLButtonElement).disabled).toBe(false);
  // The i18n mock echoes keys, so the plan label falls back to the catalog text.
  expect(props.setSummary).toHaveBeenLastCalledWith("Pipeline with Gemini · Google Gemini");
});

it("'pick everything myself' restores the full list and the local path", async () => {
  plansState.fail = false;
  plansState.plans = [GEMINI_PLAN];
  providersState.providers = [
    provider({ id: "gemini", label: "Google Gemini", secret_keys: ["gemini_api_key"], secrets_set: { gemini_api_key: false } }),
    provider({ id: "b", label: "B" }),
  ];
  stubFetch(null);
  renderStep();
  await screen.findByTestId("onboarding-plan-custom");
  expect(screen.queryByText("onboarding.api_keys.local_title")).toBeNull();
  fireEvent.click(screen.getByTestId("onboarding-plan-custom"));
  await waitFor(() => expect(screen.getByTestId("onboarding-provider-b")).toBeDefined());
  expect(screen.getByText("onboarding.api_keys.local_title")).toBeDefined();
  expect(plansState.selectStarterPlan).toHaveBeenCalledWith("custom");
});

it("with Ollama running but empty, explains the missing model and shows no activate button", async () => {
  stubFetch({ source: "live", models: [] });
  renderStep();
  await screen.findByText("onboarding.api_keys.local_detected_empty");
  expect(screen.queryByRole("button", { name: "onboarding.api_keys.local_use_button" })).toBeNull();
});
