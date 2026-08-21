import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

/**
 * The board's bottom row has two forms (`lib/deckRest.ts`, `DeckRestStrip`):
 * five cards while something is happening, one strip of readouts when nothing
 * is. This file is about the SWITCH between them — that the strip really
 * takes the three bottom slots' place, that the row above gets the height,
 * and that the chevron brings the cards back.
 *
 * The rule itself is unit-tested in `lib/deckRest.test.ts`; here it is stubbed
 * so a test can put the board in either state without a backend.
 */
const rest = vi.hoisted(() => ({ atRest: true }));
vi.mock("@/components/deck/DeckRestStrip", () => ({
  useDeckRest: () => ({ atRest: rest.atRest, segments: [] }),
  DeckRestStrip: ({ onExpand }: { onExpand: () => void }) => (
    <section data-testid="rest-strip">
      <button type="button" data-testid="rest-expand" onClick={onExpand}>
        expand
      </button>
    </section>
  ),
}));

vi.mock("@/components/layout/DockRail", () => ({ DockRail: () => <nav data-testid="dock" /> }));
vi.mock("@/components/deck/DeckWiki", () => ({
  WikiCard: ({ className }: { className?: string }) => <section className={className}>wiki</section>,
  warmWikiScene: () => {},
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
vi.mock("@/hooks/useVoiceEngineDisplay", () => ({
  useVoiceEngineDisplay: () => ({
    tier: "pipeline",
    providerId: "openrouter",
    providerLabel: "OpenRouter",
    model: "",
  }),
}));

import { MissionDeckView } from "@/views/MissionDeckView";
import { useDeckStore } from "@/store/deck";
import { useEventStore } from "@/store/events";

/** The board straight away — this file is not about the boot sequence. */
function onTheBoard() {
  useEventStore.setState({
    connected: true,
    voiceReady: true,
    wsWarming: false,
    voiceState: "idle",
    brainProvider: "openrouter",
    brainModel: "",
    assistantName: "Nova",
    messages: [],
    thinkingSteps: [],
    chatThinking: false,
    activeSection: "chats",
  });
  useDeckStore.getState().openBoard();
}

const BOTTOM_SLOTS = ["deck-slot-left-bottom", "deck-slot-right-bottom"];

describe("MissionDeckView — the bottom row at rest", { timeout: 15_000 }, () => {
  beforeEach(() => {
    useDeckStore.getState().resetDeck();
    onTheBoard();
    rest.atRest = true;
  });
  afterEach(() => cleanup());

  test("at rest the strip takes the whole bottom row, and the cards stand down", async () => {
    render(<MissionDeckView />);
    const board = await screen.findByTestId("deck-board");
    expect(screen.getByTestId("rest-strip")).toBeTruthy();
    for (const slot of BOTTOM_SLOTS) expect(screen.queryByTestId(slot)).toBeNull();
    // The strip's slot spans the board's three columns…
    expect(screen.getByTestId("deck-slot-centre-bottom").className).toContain("lg:col-span-3");
    // …and the row it sits in only takes the height it needs, so the log,
    // the orb and the wiki grow into what it gave up.
    expect(board.className).toContain("lg:grid-rows-[minmax(0,1fr)_auto]");
  });

  test("something happening brings the five cards back", async () => {
    rest.atRest = false;
    render(<MissionDeckView />);
    const board = await screen.findByTestId("deck-board");
    expect(screen.queryByTestId("rest-strip")).toBeNull();
    for (const slot of BOTTOM_SLOTS) expect(screen.getByTestId(slot)).toBeTruthy();
    expect(board.className).toContain("lg:grid-rows-[minmax(0,1fr)_minmax(0,0.6fr)]");
  });

  test("the chevron opens the full row while the board stays quiet", async () => {
    render(<MissionDeckView />);
    await screen.findByTestId("deck-board");
    fireEvent.click(screen.getByTestId("rest-expand"));
    await waitFor(() => expect(screen.getByTestId("deck-slot-left-bottom")).toBeTruthy());
    expect(screen.queryByTestId("rest-strip")).toBeNull();
  });
});
