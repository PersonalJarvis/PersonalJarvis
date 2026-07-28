/**
 * The merged voice section is named after the user, not after the product.
 *
 * Its sidebar row resolves `nav.voice`, whose locale value carries the `{name}`
 * token, so the row reads "Nova Voice" on an install whose wake word is "Hey
 * Nova". Two ways that can silently break: the token stops being interpolated
 * (the row then literally says "{name} Voice"), or someone "fixes" the label by
 * hardcoding a name into it. Both are invisible to the type checker, and both
 * would put a trademarked name in front of every user.
 *
 * The row also fronts five sections at once — dictation, the dictionary, the
 * shortcuts, the language and the speech-to-text keys — so it must stay
 * highlighted for all of them.
 *
 * The label is only the entrance: the body copy behind it has to hold the same
 * rule, so this file also covers the section's "API Keys" tab and the locale
 * values it renders.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import enJson from "@/i18n/locales/en.json";
import deJson from "@/i18n/locales/de.json";
import esJson from "@/i18n/locales/es.json";

// usePluginAttention polls /api/marketplace/plugins — mocked so the sidebar
// renders without a fetch.
vi.mock("@/hooks/usePluginAttention", () => ({
  usePluginAttention: () => ({ count: 0, names: [] }),
}));

// useVoiceMode fetches /api/settings/voice-mode — same reason. The shape covers
// both consumers in this file: the sidebar's footer card and the API-Keys tab's
// engine switch.
vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: "pipeline",
    realtimeAvailable: false,
    statusKnown: true,
    transitioning: false,
    setMode: vi.fn(),
    isLoading: false,
    isSaving: false,
    activeProvider: null,
    activeProviderLabel: null,
    activeModel: null,
    sessionActive: false,
    activeSessionMode: null,
    activeSessionProvider: "",
    activeSessionModel: "",
  }),
}));

// Only the two data hooks are replaced; `sectionHealthForSubject` and the
// provider-switch helpers stay real, so the rendered markup is the production
// one and the assertions below describe what a user actually sees.
vi.mock("@/hooks/useProviders", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/useProviders")>();
  // Stable identities: `useTierHealth` memoises on the health map, and a fresh
  // object literal per render would invalidate it on every pass.
  const noop = vi.fn();
  const emptyHealth = { health: {} };
  // Built on first call, not here: the fixture below is still in its temporal
  // dead zone while this factory runs.
  let providers: (typeof STT_PROVIDER)[] | null = null;
  return {
    ...actual,
    useProviders: () => ({
      providers: (providers ??= [STT_PROVIDER]),
      loading: false,
      error: null,
      refetch: noop,
      setActiveOptimistic: noop,
    }),
    useSectionHealth: () => emptyHealth,
  };
});

import { Sidebar } from "@/components/layout/Sidebar";
import { VoiceApiKeysTab } from "@/views/voice/VoiceApiKeysTab";
import type { ProviderDescriptor } from "@/hooks/useProviders";
import { useEventStore, type SectionId } from "@/store/events";

/**
 * A deliberately name-free speech-to-text provider: every occurrence of the
 * pinned brand in the rendered output then comes from the locale copy under
 * test, never from fixture data.
 */
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

/** An arbitrary brand — never the host's live wake-word configuration. */
const PINNED_BRAND = "Nova";

/**
 * Every product name that must never reach a user-visible string. Kept as
 * fragments so a compound ("Personal Jarvis", "Jarvis-Agent") is caught too.
 */
const FORBIDDEN_NAMES = ["jarvis"] as const;

/** Holds for a locale source value as well as for rendered output. */
function expectNoHardcodedBrand(text: string, where: string): void {
  for (const forbidden of FORBIDDEN_NAMES) {
    expect(
      text.toLowerCase().includes(forbidden),
      `${where} must not hardcode "${forbidden}": ${text}`,
    ).toBe(false);
  }
}

/**
 * Rendered output only: the raw locale value is SUPPOSED to carry `{name}`, so
 * this is the check that the substitution actually ran on the way to the DOM.
 */
function expectBrandInterpolated(text: string, where: string): void {
  expectNoHardcodedBrand(text, where);
  expect(text, `${where} must interpolate the {name} token`).not.toContain("{name}");
}

function renderSidebar() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Sidebar />
    </QueryClientProvider>,
  );
}

function renderVoiceApiKeysTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <VoiceApiKeysTab />
    </QueryClientProvider>,
  );
}

/** Every section the one voice row fronts. */
const VOICE_SECTIONS: readonly SectionId[] = [
  "dictation",
  "dictionary",
  "voice-shortcuts",
  "voice-language",
  "voice-api-keys",
];

describe("voice section sidebar brand", () => {
  beforeEach(() => {
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
      connected: true,
      activeSection: "chats",
      assistantName: PINNED_BRAND,
    });
  });

  afterEach(() => {
    cleanup();
    useEventStore.setState({ assistantName: "Assistant" });
  });

  it("names the row after the configured assistant", () => {
    renderSidebar();

    const label = screen.getByTestId("nav-row-dictation").textContent?.trim() ?? "";
    expect(label).toBe(`${PINNED_BRAND} Voice`);
    expectBrandInterpolated(label, "the voice sidebar row");
  });

  it("falls back to the neutral default when no name is configured", () => {
    useEventStore.setState({ assistantName: "Assistant" });
    renderSidebar();

    const label = screen.getByTestId("nav-row-dictation").textContent?.trim() ?? "";
    expect(label).toBe("Assistant Voice");
    expectBrandInterpolated(label, "the neutral-default voice sidebar row");
  });

  it("collapses the former Dictation and Dictionary rows into one", () => {
    renderSidebar();

    expect(screen.queryByTestId("nav-row-dictionary")).toBeNull();
    expect(screen.getByTestId("nav-row-dictation")).toBeTruthy();
  });

  it("stays highlighted for every section it fronts", () => {
    for (const section of VOICE_SECTIONS) {
      useEventStore.setState({ activeSection: section });
      renderSidebar();
      // The active row carries the inset primary bar; asserting on the class
      // keeps this independent of the (translated) label.
      expect(
        screen.getByTestId("nav-row-dictation").className,
        `active section ${section} must highlight the voice row`,
      ).toContain("shadow-[inset_2px_0_0_hsl(var(--primary))]");
      cleanup();
    }
  });
});

describe("voice API-Keys tab brand", () => {
  beforeEach(() => {
    useEventStore.setState({ assistantName: PINNED_BRAND });
  });

  afterEach(() => {
    cleanup();
    useEventStore.setState({ assistantName: "Assistant" });
  });

  it("keeps every rendered word free of a hardcoded assistant name", () => {
    const { container } = renderVoiceApiKeysTab();

    expectBrandInterpolated(container.textContent ?? "", "the voice API-Keys tab body copy");
  });

  it("really substitutes the configured brand into its body copy", () => {
    renderVoiceApiKeysTab();

    // Proves the copy still SPEAKS about the assistant — a description that
    // dropped the reference entirely would pass the negative check above while
    // silently losing the sentence's subject.
    const body = screen.getByTestId("voice-api-keys-tab").textContent ?? "";
    expect(body).toContain(PINNED_BRAND);
  });

  it("follows a rename without a remount", () => {
    const { container } = renderVoiceApiKeysTab();
    expect(container.textContent).toContain(PINNED_BRAND);

    act(() => {
      useEventStore.setState({ assistantName: "Atlas" });
    });

    expect(container.textContent).toContain("Atlas");
    expectBrandInterpolated(container.textContent ?? "", "the renamed voice API-Keys tab");
  });
});

/**
 * The locale-level root cause. The rendered checks above only see the keys this
 * one tab happens to mount; these pin the values themselves, in all three
 * locales, so a translation cannot reintroduce the literal name through a
 * surface no test renders.
 */
describe("voice locale copy", () => {
  const LOCALES = { en: enJson, de: deJson, es: esJson } as const;

  // The descriptions the merged voice section and the API-Keys view share.
  const NAME_BEARING_KEYS = [
    "voice_engine_desc",
    "cat_tts_desc",
    "cat_stt_desc",
  ] as const;

  for (const [locale, bundle] of Object.entries(LOCALES)) {
    const strings = (bundle as { apikeys_view: Record<string, string> }).apikeys_view;

    for (const key of NAME_BEARING_KEYS) {
      it(`${locale}: apikeys_view.${key} refers to the assistant by token`, () => {
        const value = strings[key];
        expect(value, `apikeys_view.${key} is missing from ${locale}.json`).toBeTruthy();
        expect(value).toContain("{name}");
        expectNoHardcodedBrand(value, `${locale}.json apikeys_view.${key}`);
      });
    }
  }
});
