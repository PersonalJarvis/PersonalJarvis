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
 */
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// usePluginAttention polls /api/marketplace/plugins — mocked so the sidebar
// renders without a fetch.
vi.mock("@/hooks/usePluginAttention", () => ({
  usePluginAttention: () => ({ count: 0, names: [] }),
}));

// useVoiceMode fetches /api/settings/voice-mode — same reason.
vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({
    mode: "pipeline",
    activeProvider: null,
    activeProviderLabel: null,
    activeModel: null,
    sessionActive: false,
    activeSessionMode: null,
    activeSessionProvider: "",
    activeSessionModel: "",
  }),
}));

import { Sidebar } from "@/components/layout/Sidebar";
import { useEventStore, type SectionId } from "@/store/events";

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
      assistantName: "Nova",
    });
  });

  afterEach(() => {
    cleanup();
    useEventStore.setState({ assistantName: "Assistant" });
  });

  it("names the row after the configured assistant", () => {
    renderSidebar();

    const label = screen.getByTestId("nav-row-dictation").textContent?.trim() ?? "";
    expect(label).toBe("Nova Voice");
    expect(label).not.toContain("{name}");
    expect(label.toLowerCase()).not.toContain("jarvis");
  });

  it("falls back to the neutral default when no name is configured", () => {
    useEventStore.setState({ assistantName: "Assistant" });
    renderSidebar();

    const label = screen.getByTestId("nav-row-dictation").textContent?.trim() ?? "";
    expect(label).toBe("Assistant Voice");
    expect(label.toLowerCase()).not.toContain("jarvis");
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
