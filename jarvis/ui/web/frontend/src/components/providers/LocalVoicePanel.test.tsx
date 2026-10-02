/**
 * The Local voice card body. What it must never do is claim a readiness the
 * backend did not report: the phase line, the self-test verdict and the
 * "not verified on this system" note all come from `/local-voice/status`.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { LocalVoicePanel } from "@/components/providers/LocalVoicePanel";
import type { LocalVoiceStatus } from "@/lib/localVoice";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
  useUiLanguage: () => "en",
}));

const NOT_INSTALLED: LocalVoiceStatus = {
  phase: "not_installed",
  stage: "",
  progress: 0,
  reason: "Local voice is not set up on this machine yet.",
  installed: false,
  setup: {
    running: false,
    stage: "",
    progress: 0,
    detail: "",
    error: "",
    warnings: [],
    finished_at: null,
  },
  llm_model: "qwen3.5:4b",
  llm_source: "default",
  llm_installed: false,
  llm_error: "",
  llm_choices: ["llama3.2:3b"],
  voice: "pocket",
  voices: ["pocket", "piper"],
  machine_class: "apple",
  expected_latency: { low_s: 1.2, high_s: 1.8, basis: "estimate" },
  platform: "macos",
  os_verified: false,
  selftest: null,
  selftest_running: false,
};

function serve(handler: (url: string, init?: RequestInit) => LocalVoiceStatus) {
  const calls: Array<{ url: string; method: string }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, method: init?.method ?? "GET" });
    const body = handler(url, init);
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch = fetchMock as unknown as typeof fetch;
  return calls;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("LocalVoicePanel", () => {
  it("shows the phase, the machine, the estimate and the unverified note", async () => {
    serve(() => NOT_INSTALLED);
    render(<LocalVoicePanel onChanged={() => {}} />);

    expect(await screen.findByText("apikeys_view.local_voice_phase_not_installed")).toBeTruthy();
    expect(screen.getByTestId("local-voice-phase").dataset.phase).toBe("not_installed");
    expect(screen.getByTestId("local-voice-machine").textContent).toBe(
      "apikeys_view.local_voice_machine_apple",
    );
    expect(screen.getByTestId("local-voice-latency").textContent).toBe(
      "1.2–1.8 s · apikeys_view.local_voice_basis_estimate",
    );
    expect(screen.getByTestId("local-voice-unverified")).toBeTruthy();
    expect(screen.getByTestId("local-voice-llm-missing")).toBeTruthy();
    expect(screen.queryByTestId("local-voice-selftest")).toBeNull();
  });

  it("starts setup and then follows its progress", async () => {
    const installing: LocalVoiceStatus = {
      ...NOT_INSTALLED,
      phase: "installing",
      stage: "models",
      progress: 0.42,
      setup: { ...NOT_INSTALLED.setup, running: true, stage: "models", progress: 0.42 },
    };
    let started = false;
    const calls = serve((url) => {
      if (url.endsWith("/setup")) started = true;
      return started ? installing : NOT_INSTALLED;
    });
    render(<LocalVoicePanel onChanged={() => {}} />);

    fireEvent.click(await screen.findByTestId("local-voice-setup"));
    await waitFor(() => expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("42"));
    expect(calls.some((c) => c.url.endsWith("/local-voice/setup") && c.method === "POST")).toBe(true);
    expect(screen.getByText("apikeys_view.local_voice_stage_models")).toBeTruthy();
    expect(screen.queryByTestId("local-voice-setup")).toBeNull();
  });

  it("shows the backend's own reason when the engine failed", async () => {
    serve(() => ({
      ...NOT_INSTALLED,
      phase: "failed",
      installed: true,
      reason: "The local voice could not load: Ollama did not answer.",
    }));
    render(<LocalVoicePanel onChanged={() => {}} />);

    expect(await screen.findByText("The local voice could not load: Ollama did not answer.")).toBeTruthy();
  });

  it("offers the self-test once installed and shows a stale result honestly", async () => {
    serve(() => ({
      ...NOT_INSTALLED,
      phase: "stopped",
      reason: "",
      installed: true,
      os_verified: true,
      llm_installed: true,
      selftest: {
        ok: true,
        at: 1,
        stale: true,
        reason: "",
        languages: { de: { voice: "pocket-german", synth_ms: 210, stt_ms: 140, ok: true } },
        llm: { ok: true, ms: 390 },
      },
    }));
    render(<LocalVoicePanel onChanged={() => {}} />);

    expect(await screen.findByTestId("local-voice-selftest")).toBeTruthy();
    expect(screen.getByTestId("local-voice-selftest-result").dataset.ok).toBe("true");
    expect(screen.getByTestId("local-voice-selftest-stale")).toBeTruthy();
    expect(screen.queryByTestId("local-voice-unverified")).toBeNull();
    expect(screen.queryByTestId("local-voice-setup")).toBeNull();
  });
});
