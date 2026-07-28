/**
 * Component tests for the voice section's "API Keys" tab.
 *
 * The tab is deliberately NOT a second implementation: it renders the same
 * extracted provider block the API-Keys view uses, scoped to the `stt` tier.
 * These tests pin the two things that would silently break that contract —
 * that a speech-to-text provider card actually renders here, and that none of
 * the API-Keys view's own category tabs come along for the ride (this screen
 * shows one tier, so a tab strip would be a rendering fault).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import type { ProviderDescriptor } from "@/hooks/useProviders";

// Deterministic data: no network round-trip, no react-query provider needed.
const STT_PROVIDER: ProviderDescriptor = {
  id: "test-speech",
  label: "Test Speech Service",
  tier: "stt",
  auth_mode: "api_key",
  secret_keys: ["TEST_SPEECH_API_KEY"],
  secrets_set: { TEST_SPEECH_API_KEY: false },
  dashboard_url: null,
  login_cli: null,
  install_hint: null,
  credential_path_hint: null,
  configured: false,
  active: false,
  cli_installed: null,
  credential_help: null,
  signup_url: null,
  billing: "api",
  alt_credential: null,
};

const BRAIN_PROVIDER: ProviderDescriptor = {
  ...STT_PROVIDER,
  id: "test-brain",
  label: "Test Brain Service",
  tier: "brain",
  secret_keys: ["TEST_BRAIN_API_KEY"],
  secrets_set: { TEST_BRAIN_API_KEY: false },
};

vi.mock("@/hooks/useProviders", () => ({
  sectionHealthForSubject: (
    health: { subject_id?: string } | undefined,
    subjectId?: string,
  ) => (subjectId && health?.subject_id === subjectId ? health : undefined),
  useProviders: () => ({
    providers: [STT_PROVIDER, BRAIN_PROVIDER],
    loading: false,
    error: null,
    refetch: vi.fn(),
    setActiveOptimistic: vi.fn(),
  }),
  useSectionHealth: () => ({ health: {} }),
}));

vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: "pipeline",
    realtimeAvailable: false,
    statusKnown: true,
    sessionActive: false,
    activeSessionMode: null,
    activeSessionProvider: "",
    activeSessionModel: "",
    transitioning: false,
    setMode: vi.fn(),
    isLoading: false,
    isSaving: false,
  }),
}));

import { VoiceApiKeysTab } from "@/views/voice/VoiceApiKeysTab";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("VoiceApiKeysTab", () => {
  it("renders the speech-to-text provider card", () => {
    render(<VoiceApiKeysTab />);

    expect(screen.getByTestId("voice-api-keys-tab")).toBeTruthy();
    expect(screen.getByText("Test Speech Service")).toBeTruthy();
    expect(screen.getByLabelText("Enter TEST_SPEECH_API_KEY")).toBeTruthy();
  });

  it("shows only the speech-to-text tier, never other tiers' providers", () => {
    render(<VoiceApiKeysTab />);

    expect(screen.queryByText("Test Brain Service")).toBeNull();
  });

  it("brings none of the API-Keys view's category tabs along", () => {
    render(<VoiceApiKeysTab />);

    expect(screen.queryByTestId("api-keys-category-tabs")).toBeNull();
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
  });

  it("keeps the engine switch, so the tier's relevance stays visible", () => {
    render(<VoiceApiKeysTab />);

    expect(screen.getByTestId("voice-engine-header-control")).toBeTruthy();
  });

  it("stands its own header down when the merged section owns it", () => {
    const { rerender } = render(<VoiceApiKeysTab />);
    const withHeader = screen.getAllByRole("heading").length;

    rerender(<VoiceApiKeysTab hideHeader />);
    expect(screen.getAllByRole("heading").length).toBe(withHeader - 1);
  });
});
