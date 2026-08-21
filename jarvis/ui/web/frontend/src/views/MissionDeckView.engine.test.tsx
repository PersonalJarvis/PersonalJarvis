import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

/**
 * The mission-deck header and orb name the voice engine that will answer
 * the next spoken turn. Pipeline and realtime are independent picks; showing
 * the dormant classic brain while Vertex AI Live is selected is the bug
 * this file pins.
 */
vi.mock("@/components/layout/DockRail", () => ({ DockRail: () => <nav data-testid="dock" /> }));
vi.mock("@/components/deck/DeckWiki", () => ({
  WikiCard: ({ className }: { className?: string }) => <section className={className}>wiki</section>,
}));
vi.mock("@/components/deck/DeckActivityCards", () => ({
  IdeGridCard: () => <section>ide</section>,
  OutputsCard: () => <section>outputs</section>,
  RunsCard: () => <section>runs</section>,
  TerminalsCard: () => <section>terminals</section>,
}));
vi.mock("@/components/deck/DeckSignalCards", () => ({
  ApiStatsCard: () => <section>api</section>,
  CaptureCard: () => <section>capture</section>,
  LiveCounter: () => <div>counter</div>,
}));
// Stubbed for the same reason as the cards above — the real hook reads the
// outputs, runs and IDE queries and needs a QueryClient. This file is about
// which engine the header names.
vi.mock("@/components/deck/DeckRestStrip", () => ({
  useDeckRest: () => ({ atRest: false, segments: [] }),
  DeckRestStrip: () => <section>rest</section>,
}));
vi.mock("@/components/deck/DeckTurnCard", () => ({ TurnCard: () => <section>turn</section> }));
vi.mock("@/components/deck/DeckLogCard", async () => {
  const actual = await vi.importActual<typeof import("@/components/deck/DeckLogCard")>(
    "@/components/deck/DeckLogCard",
  );
  return { ...actual, LogCard: () => <section>log</section> };
});
vi.mock("@/hooks/useWakeWord", () => ({
  useWakeWord: () => ({
    config: {
      phrase: "Hey Nova",
      engine: "openwakeword",
      custom_model_path: "",
      fuzzy_match_ratio: 0.8,
      language: "auto",
      engines: ["openwakeword"],
      instant_phrases: [],
      local_whisper_available: false,
      enabled: true,
    },
    loading: false,
    error: null,
    refetch: async () => {},
    saveWakeWord: async () => ({}),
    setWakeLanguage: async () => {},
    setWakeActivation: async () => ({}),
  }),
}));
vi.mock("@/lib/voiceApi", () => ({
  requestVoiceCall: async () => ({ armed: true }),
  requestVoiceHangup: async () => {},
}));
vi.mock("@/components/MascotGigi", () => ({
  MascotGigi: () => <div data-testid="mascot-gigi" />,
}));
vi.mock("@/components/layout/TopBar", () => ({
  TopBarActions: () => null,
}));
vi.mock("@/components/layout/CodingModeBadge", () => ({
  CodingModeBadge: () => null,
}));

const voiceModeMock = vi.hoisted(() => ({
  value: {
    mode: "pipeline" as "pipeline" | "realtime",
    activeProvider: null as string | null,
    activeProviderLabel: null as string | null,
    activeModel: null as string | null,
    sessionActive: false,
    activeSessionMode: null as "pipeline" | "realtime" | null,
    activeSessionProvider: "",
    activeSessionModel: "",
  },
}));
vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => voiceModeMock.value,
}));

import { MissionDeckView } from "@/views/MissionDeckView";
import { useDeckStore } from "@/store/deck";
import { useEventStore } from "@/store/events";

function resetVoiceMode() {
  voiceModeMock.value = {
    mode: "pipeline",
    activeProvider: null,
    activeProviderLabel: null,
    activeModel: null,
    sessionActive: false,
    activeSessionMode: null,
    activeSessionProvider: "",
    activeSessionModel: "",
  };
}

function readyBoard() {
  useEventStore.setState({
    connected: true,
    voiceReady: true,
    wsWarming: false,
    voiceState: "idle",
    brainProvider: "openrouter",
    brainModel: "google/gemini-3.5-flash",
    assistantName: "Nova",
    messages: [{ id: "m1", role: "assistant", content: "Hallo.", ts: Date.now() }],
    thinkingSteps: [],
    chatThinking: false,
    activeSection: "chats",
  });
  useDeckStore.getState().openBoard();
}

describe("MissionDeckView — voice-engine header and orb", () => {
  beforeEach(() => {
    useDeckStore.getState().resetDeck();
    resetVoiceMode();
    readyBoard();
  });
  afterEach(() => cleanup());

  test("pipeline mode names the classic brain on the header and the orb", () => {
    render(<MissionDeckView />);
    expect(screen.getByTestId("deck-stat-engine").textContent).toBe("BrainOpenRouter");
    expect(screen.getByTestId("deck-stat-model").textContent).toBe("Modelgoogle/gemini-3.5-flash");
    expect(screen.getByTestId("deck-orb-provider").textContent).toBe("OpenRouter");
  });

  test("realtime mode names Vertex AI Live, not the dormant OpenRouter brain", () => {
    voiceModeMock.value = {
      ...voiceModeMock.value,
      mode: "realtime",
      activeProvider: "vertex-live",
      activeProviderLabel: "Vertex AI Live",
      activeModel: "gemini-live-2.5-flash-preview-native-audio-dialog",
    };
    render(<MissionDeckView />);
    expect(screen.getByTestId("deck-stat-engine").textContent).toBe("RealtimeVertex AI Live");
    expect(screen.getByTestId("deck-stat-model").textContent).toBe(
      "Modelgemini-live-2.5-flash-preview-native-audio-dialog",
    );
    expect(screen.getByTestId("deck-orb-provider").textContent).toBe("Vertex AI Live");
    expect(screen.queryByText("OpenRouter")).toBeNull();
  });

  test("a running realtime session outranks the configured realtime pick", () => {
    voiceModeMock.value = {
      ...voiceModeMock.value,
      mode: "realtime",
      activeProvider: "vertex-live",
      activeProviderLabel: "Vertex AI Live",
      activeModel: "gemini-live-2.5-flash-preview-native-audio-dialog",
      sessionActive: true,
      activeSessionMode: "realtime",
      activeSessionProvider: "openai-realtime",
      activeSessionModel: "gpt-realtime-2.1",
    };
    render(<MissionDeckView />);
    expect(screen.getByTestId("deck-stat-engine").textContent).toBe("RealtimeOpenAI Realtime");
    expect(screen.getByTestId("deck-stat-model").textContent).toBe("Modelgpt-realtime-2.1");
    expect(screen.getByTestId("deck-orb-provider").textContent).toBe("OpenAI Realtime");
  });

  test("clicking the engine stat opens API Keys", () => {
    render(<MissionDeckView />);
    fireEvent.click(screen.getByTestId("deck-stat-engine"));
    expect(useEventStore.getState().activeSection).toBe("apikeys");
  });
});
