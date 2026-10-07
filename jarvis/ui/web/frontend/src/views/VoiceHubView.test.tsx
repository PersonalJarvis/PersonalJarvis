import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mutable mock store state — hoisted so the vi.mock factory below can close over
// it. Each test sets `activeSection` before rendering and inspects the
// `setActiveSection` spy.
const { mockState } = vi.hoisted(() => ({
  mockState: {
    activeSection: "dictation" as string,
    setActiveSection: vi.fn(),
  },
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: typeof mockState) => unknown) => selector(mockState),
}));

vi.mock("@/i18n", () => ({
  // Identity translator: returns the key so assertions can match exact labels.
  useT: () => (key: string) => key,
}));

// ViewHeader lives in ChatsView, which drags in the whole chat surface. The hub
// only needs the header's shape, so stub it — and give it a testid so "exactly
// one header" is assertable.
vi.mock("@/views/ChatsView", () => ({
  ViewHeader: ({ title, subtitle }: { title: string; subtitle?: string }) => (
    <header data-testid="view-header">
      <span data-testid="view-header-title">{title}</span>
      <span data-testid="view-header-subtitle">{subtitle}</span>
    </header>
  ),
}));

// Stub every embedded tab — VoiceHubView is a thin wrapper; we assert which
// child it renders and that it tells that child to stand its own header down,
// not the children's behaviour.
function stub(name: string) {
  return ({ hideHeader }: { hideHeader?: boolean }) => (
    <div data-testid={name} data-hide-header={hideHeader ? "true" : "false"}>
      {name}
    </div>
  );
}

vi.mock("@/views/DictationView", () => ({ DictationView: stub("DICTATION_CONTENT") }));
vi.mock("@/views/DictionaryView", () => ({
  DictionaryView: stub("DICTIONARY_CONTENT"),
}));
vi.mock("@/views/voice/ShortcutsTab", () => ({
  ShortcutsTab: stub("SHORTCUTS_CONTENT"),
}));
vi.mock("@/views/voice/LanguageTab", () => ({ LanguageTab: stub("LANGUAGE_CONTENT") }));
vi.mock("@/views/voice/VoiceApiKeysTab", () => ({
  VoiceApiKeysTab: stub("API_KEYS_CONTENT"),
}));

import { VoiceHubView } from "@/views/VoiceHubView";

beforeEach(() => {
  mockState.activeSection = "dictation";
  mockState.setActiveSection = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("VoiceHubView tab switching", () => {
  it("lands on Dictation and marks its tab current", () => {
    mockState.activeSection = "dictation";
    render(<VoiceHubView />);

    expect(screen.getByTestId("DICTATION_CONTENT")).toBeTruthy();
    expect(screen.queryByTestId("DICTIONARY_CONTENT")).toBeNull();

    expect(
      screen.getByRole("button", { name: "nav.dictation" }).getAttribute("aria-current"),
    ).toBe("page");
    // All five tabs are always present, whichever one is active.
    for (const key of [
      "nav.dictation",
      "nav.dictionary",
      "nav.voice_shortcuts",
      "nav.voice_language",
      "nav.voice_api_keys",
    ]) {
      expect(screen.getByRole("button", { name: key })).toBeTruthy();
    }
  });

  it("activates the voice-shortcuts section when its tab is clicked", () => {
    mockState.activeSection = "dictation";
    render(<VoiceHubView />);

    fireEvent.click(screen.getByRole("button", { name: "nav.voice_shortcuts" }));
    expect(mockState.setActiveSection).toHaveBeenCalledWith("voice-shortcuts");
  });

  it("shows the API-Keys tab when activeSection is 'voice-api-keys'", () => {
    mockState.activeSection = "voice-api-keys";
    render(<VoiceHubView />);

    expect(screen.getByTestId("API_KEYS_CONTENT")).toBeTruthy();
    expect(screen.queryByTestId("DICTATION_CONTENT")).toBeNull();
    expect(
      screen
        .getByRole("button", { name: "nav.voice_api_keys" })
        .getAttribute("aria-current"),
    ).toBe("page");
  });

  it("falls back to Dictation for an unexpected section id", () => {
    mockState.activeSection = "chats";
    render(<VoiceHubView />);

    expect(screen.getByTestId("DICTATION_CONTENT")).toBeTruthy();
  });
});

describe("VoiceHubView single header", () => {
  it("renders exactly one header, titled from the brand-carrying nav.voice key", () => {
    render(<VoiceHubView />);

    // Two stacked bordered bands (hub header + child header) read as a
    // rendering fault — the section title is drawn once, here.
    expect(screen.getAllByTestId("view-header")).toHaveLength(1);
    expect(screen.getByTestId("view-header-title").textContent).toBe("nav.voice");
    expect(screen.getByTestId("view-header-subtitle").textContent).toBe(
      "voice.hub.subtitle",
    );
  });

  it("tells the mounted tab to stand its own header down", () => {
    for (const [section, testid] of [
      ["dictation", "DICTATION_CONTENT"],
      ["dictionary", "DICTIONARY_CONTENT"],
      ["voice-shortcuts", "SHORTCUTS_CONTENT"],
      ["voice-language", "LANGUAGE_CONTENT"],
      ["voice-api-keys", "API_KEYS_CONTENT"],
    ] as const) {
      mockState.activeSection = section;
      render(<VoiceHubView />);
      expect(screen.getByTestId(testid).getAttribute("data-hide-header")).toBe("true");
      cleanup();
    }
  });
});
