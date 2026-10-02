/**
 * Tests for the WakeWordPanel (rendered via SettingsView).
 * Key assertions:
 *   - No quick-pick chips rendered.
 *   - Placeholder text is "e.g. Jonas" (from i18n key settings_view.wake_word.phrase_placeholder).
 *   - The phrase input starts empty when the backend returns phrase == "".
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// i18n mock: use the real strings from en.json via a pass-through so we can
// assert on actual English copy rather than key strings.
// ---------------------------------------------------------------------------
vi.mock("@/i18n", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/i18n")>();
  return {
    ...actual,
    useT: () => actual.useT(),
    useUiLanguage: () => "en",
    useReplyLanguage: () => "auto",
  };
});

// ---------------------------------------------------------------------------
// Silence hooks that make real network calls unrelated to wake-word:
//   useWebSocket, useBrainStatus, useKeybinds, useProviders,
//   useAutostart, and the event store's pushToast.
// ---------------------------------------------------------------------------
vi.mock("@/hooks/useWebSocket", () => ({ useWebSocket: vi.fn() }));
vi.mock("@/hooks/useBrainStatus", () => ({ useBrainStatus: vi.fn() }));
vi.mock("@/hooks/useHotkey", () => ({
  useKeybinds: () => ({
    config: null,
    loading: true,
    error: null,
    saveKeybind: vi.fn(),
  }),
  chordToCombo: vi.fn(),
  codeToKeyToken: vi.fn(),
  composeCombo: vi.fn(() => ""),
  comboTokens: vi.fn(() => new Set<string>()),
  validateCombo: vi.fn(() => ({ status: "empty" })),
}));

// Silence settings sub-groups that hit their own endpoints.
vi.mock("@/views/settings/OverlayTaskbarGroup", () => ({
  OverlayTaskbarGroup: () => null,
}));
vi.mock("@/views/settings/LanguagesGroup", () => ({
  LanguagesGroup: () => null,
}));
vi.mock("@/views/settings/AppSettingsGroup", () => ({
  AppSettingsGroup: () => null,
}));
vi.mock("@/views/settings/JarvisApiGroup", () => ({
  JarvisApiGroup: () => null,
}));
// RealtimeVoiceGroup uses react-query (useVoiceMode) and needs a
// QueryClientProvider this test doesn't set up — silence it like the other
// sibling groups above, unrelated to wake-word.
vi.mock("@/views/settings/RealtimeVoiceGroup", () => ({
  RealtimeVoiceGroup: () => null,
}));

// Silence the onboarding gate so it doesn't interfere with SettingsView tests.
vi.mock("@/components/onboarding/OnboardingGate", () => ({
  OnboardingGate: () => null,
}));

import { SettingsView } from "@/views/SettingsView";

afterEach(cleanup);

function makeWakeWordGET(phrase: string) {
  return {
    phrase,
    engine: "auto",
    custom_model_path: "",
    fuzzy_match_ratio: 80,
    engines: ["auto", "openwakeword"],
    instant_phrases: ["Hey Jarvis", "Jarvis"],
    local_whisper_available: false,
  };
}

describe("WakeWordPanel (via SettingsView)", () => {
  it("renders no quick-pick chips even when instant_phrases are returned", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(makeWakeWordGET("")),
      }),
    );

    render(<SettingsView />);

    await waitFor(() => {
      expect(screen.getByText("Wake Word")).toBeDefined();
    });

    // Quick-pick chips used to be rendered as buttons with the phrase text.
    // After the refactor there must be no such button.
    expect(screen.queryByRole("button", { name: "Hey Jarvis" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Jarvis" })).toBeNull();
  });

  it("shows placeholder 'e.g. Jonas' in the phrase input", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(makeWakeWordGET("")),
      }),
    );

    render(<SettingsView />);

    await waitFor(() => {
      screen.getByText("Wake Word");
    });

    // Find the wake phrase input by placeholder.
    const input = screen.queryByPlaceholderText("e.g. Jonas");
    expect(input).toBeDefined();
    expect(input).not.toBeNull();
  });

  it("starts with an empty phrase field when backend returns phrase ''", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(makeWakeWordGET("")),
      }),
    );

    render(<SettingsView />);

    await waitFor(() => {
      screen.getByText("Wake Word");
    });

    const input = screen.queryByPlaceholderText(
      "e.g. Jonas",
    ) as HTMLInputElement | null;
    expect(input).not.toBeNull();
    expect(input!.value).toBe("");
  });

  it("shows the 'Download wake model' button on a degraded save result and POSTs the download-model route on click", async () => {
    // A minimal request router: GET hydrates the form, PUT simulates the
    // backend resolving the phrase to the unreliable stt_match-only path
    // (jarvis/speech/wake_phrase.py's degrade branch), and the download route
    // simulates the Vosk model becoming available in-app.
    const calls: { url: string; method: string }[] = [];
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method });

      if (url === "/api/settings/wake-word" && method === "GET") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(makeWakeWordGET("")),
        });
      }
      if (url === "/api/settings/wake-word" && method === "PUT") {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              ok: true,
              phrase: "Jonas",
              engine: "auto",
              resolved_engine: "stt_match",
              degraded: true,
              wake_available: true,
              message:
                "Custom phrase 'Jonas' is on the local-Whisper transcript " +
                "match — this is UNRELIABLE for a hard name. Download the " +
                "Vosk model for the configured language to make it reliable " +
                "(Settings -> Wake word -> 'Download wake model').",
              persisted: true,
              restart_required: true,
            }),
        });
      }
      if (
        url === "/api/settings/wake-word/download-model" &&
        method === "POST"
      ) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              ok: true,
              present: true,
              message: "Wake model ready.",
            }),
        });
      }
      // Anything else (assistant-name refresh, restart probes, …).
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<SettingsView />);

    await waitFor(() => screen.getByText("Wake Word"));

    const input = screen.getByPlaceholderText(
      "e.g. Jonas",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Jonas" } });

    const saveButton = screen.getByRole("button", { name: "Save wake word" });
    fireEvent.click(saveButton);

    // Degraded result renders the download control (there is no such button
    // for a non-degraded save).
    const downloadButton = await screen.findByRole("button", {
      name: "Download wake model",
    });
    expect(downloadButton).toBeDefined();

    fireEvent.click(downloadButton);

    await waitFor(() => {
      expect(
        calls.some(
          (c) =>
            c.url === "/api/settings/wake-word/download-model" &&
            c.method === "POST",
        ),
      ).toBe(true);
    });
  });
});
