import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

import { ApiKeysStep } from "./ApiKeysStep";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type ProbeShape = { source: string; models: { id: string }[] } | null;

/**
 * Stub fetch for the local-path probe (+ the brain switch and the engine pin).
 * `null` = network error.
 */
function stubFetch(probe: ProbeShape, { voiceModeOk = true } = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/providers/ollama/models") {
        if (probe === null) throw new Error("network down");
        return { ok: true, json: async () => probe } as Response;
      }
      if (url === "/api/brain/switch") {
        return { ok: true, json: async () => ({ ok: true }) } as Response;
      }
      if (url === "/api/settings/voice-mode") {
        if (!voiceModeOk) {
          return {
            ok: false,
            text: async () => "engine pin refused",
          } as Response;
        }
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
    ...overrides,
  };
  render(<ApiKeysStep {...props} />);
  return props;
}

it("shows the real API Keys view with the navigation and voice-mode switch marked", () => {
  stubFetch(null);
  renderStep();

  const screenshot = screen.getByRole("img", {
    name: "onboarding.api_keys.screenshot_alt",
  });
  expect(screenshot.getAttribute("src")).toBe(
    "/onboarding/api-keys-realtime-guide.png",
  );
  expect(screen.getByTestId("api-keys-marker")).toBeDefined();
  expect(screen.getByTestId("voice-mode-marker")).toBeDefined();
  expect(screen.getByText("onboarding.api_keys.voice_mode_title")).toBeDefined();
  expect(screen.getByText("onboarding.api_keys.realtime_label")).toBeDefined();
  expect(screen.getByText("onboarding.api_keys.pipeline_label")).toBeDefined();
});

it("keeps credential entry out of the onboarding modal", async () => {
  stubFetch(null);
  renderStep();
  await screen.findByText("onboarding.api_keys.local_missing");

  // No key can ever be typed here — that is the step's security contract.
  expect(screen.queryByRole("textbox")).toBeNull();
  // The only link the step may carry is the Ollama install pointer (no
  // provider dashboards, no key pages).
  for (const link of screen.queryAllByRole("link")) {
    expect(link.getAttribute("href")).toBe("https://ollama.com/download");
  }
});

it("offers one clear action to continue onboarding", async () => {
  stubFetch(null);
  const props = renderStep();
  await screen.findByText("onboarding.api_keys.local_missing");

  const cont = screen.getByRole("button", { name: "onboarding.api_keys.continue" });
  fireEvent.click(cont);
  expect(props.goNext).toHaveBeenCalledTimes(1);
  expect(props.skip).not.toHaveBeenCalled();
});

it("probes through the backend and offers one-click local activation", async () => {
  const calls = stubFetch({ source: "live", models: [{ id: "qwen3.5:9b" }] });
  renderStep();

  await screen.findByText("onboarding.api_keys.local_detected");
  const useLocal = screen.getByRole("button", {
    name: "onboarding.api_keys.local_use_button",
  });
  fireEvent.click(useLocal);

  await screen.findByText("onboarding.api_keys.local_active");
  const switchCall = calls.find((c) => c.url === "/api/brain/switch");
  expect(switchCall).toBeDefined();
  expect(JSON.parse(String(switchCall?.init?.body))).toMatchObject({
    provider: "ollama",
  });
  // The probe went through the backend catalog route — never a
  // browser-direct localhost:11434 call.
  expect(calls[0].url).toBe("/api/providers/ollama/models");
});

it("pins the pipeline engine so the local brain is actually used", async () => {
  const calls = stubFetch({ source: "live", models: [{ id: "qwen3.5:9b" }] });
  renderStep();

  await screen.findByText("onboarding.api_keys.local_detected");
  fireEvent.click(
    screen.getByRole("button", { name: "onboarding.api_keys.local_use_button" }),
  );
  await screen.findByText("onboarding.api_keys.local_active");

  // Realtime replaces STT+Brain+TTS and never reads `[brain].primary`, so
  // activating the local brain without pinning Pipeline changes nothing the
  // user can hear — and `[voice].mode` defaults to realtime.
  const modeCall = calls.find((c) => c.url === "/api/settings/voice-mode");
  expect(modeCall?.init?.method).toBe("PUT");
  expect(JSON.parse(String(modeCall?.init?.body))).toMatchObject({
    mode: "pipeline",
    persist: true,
  });
});

it("reports a refused engine pin instead of claiming the local path is live", async () => {
  stubFetch({ source: "live", models: [{ id: "qwen3.5:9b" }] }, { voiceModeOk: false });
  renderStep();

  await screen.findByText("onboarding.api_keys.local_detected");
  fireEvent.click(
    screen.getByRole("button", { name: "onboarding.api_keys.local_use_button" }),
  );

  await screen.findByText("engine pin refused");
  expect(screen.queryByText("onboarding.api_keys.local_active")).toBeNull();
});

it("tells a running-but-empty Ollama to pull a model first", async () => {
  stubFetch({ source: "live", models: [] });
  renderStep();

  await screen.findByText("onboarding.api_keys.local_detected_empty");
  expect(
    screen.queryByRole("button", { name: "onboarding.api_keys.local_use_button" }),
  ).toBeNull();
});

it("shows the honest install pointer when no local server answers", async () => {
  stubFetch({ source: "static", models: [] });
  renderStep();

  await screen.findByText("onboarding.api_keys.local_missing");
  const link = screen.getByRole("link", {
    name: "onboarding.api_keys.local_missing_link",
  });
  expect(link.getAttribute("href")).toBe("https://ollama.com/download");
});
