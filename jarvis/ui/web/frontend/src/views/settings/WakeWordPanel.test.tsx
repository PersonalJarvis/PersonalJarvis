/**
 * Tests for the WakeWordPanel (rendered via SettingsView).
 * Key assertions:
 *   - No quick-pick chips rendered.
 *   - Placeholder text is "e.g. Jonas" (from i18n key settings_view.wake_word.phrase_placeholder).
 *   - The phrase input starts empty when the backend returns phrase == "".
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
//   useWebSocket, useBrainStatus, useAssistantName, useKeybinds, useProviders,
//   useAutostart, and the event store's pushToast.
// ---------------------------------------------------------------------------
vi.mock("@/hooks/useWebSocket", () => ({ useWebSocket: vi.fn() }));
vi.mock("@/hooks/useBrainStatus", () => ({ useBrainStatus: vi.fn() }));
vi.mock("@/hooks/useAssistantName", () => ({
  useAssistantName: () => ({
    config: null,
    loading: true,
    error: null,
    saveName: vi.fn(),
  }),
}));
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
    sensitivity: 0.5,
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
});

// ---------------------------------------------------------------------------
// Assistant Name vs Wake Word disambiguation.
//
// Forensic (2026-06-20): a user trying to switch the spoken trigger back to
// "Hey Jarvis" edited the *Assistant Name* panel (which sits right above the
// Wake Word panel and also mentions "wake word") instead of the Wake Word
// panel, so the trigger silently stayed "Hey Alex". The copy of both panels
// must make it unmistakable which one is the spoken trigger.
// ---------------------------------------------------------------------------
describe("Assistant Name vs Wake Word copy", () => {
  it("makes clear the Assistant Name panel is NOT the wake word", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(makeWakeWordGET("")),
      }),
    );

    render(<SettingsView />);

    await waitFor(() => {
      screen.getByText("Assistant Name");
    });

    // The Assistant Name description must explicitly disclaim it is the trigger.
    expect(screen.getByText(/not the wake word/i)).toBeDefined();
  });

  it("describes the Wake Word panel as the phrase you say out loud", async () => {
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

    expect(screen.getByText(/say out loud/i)).toBeDefined();
  });
});
