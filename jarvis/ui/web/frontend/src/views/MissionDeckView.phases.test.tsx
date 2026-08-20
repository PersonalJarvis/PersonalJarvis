import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

/**
 * The deck's two acts (2026-08-18): the boot sequence while the app comes up,
 * the board from there on — and never back. The standby ring that waited for
 * a spoken word between them was cut on 2026-08-20: the stage launches itself
 * on the clock, and a spoken turn only gets there sooner. The instruments
 * themselves are stubbed (each has its own tests); what is under test is
 * WHICH stage is on, and how the board is reached.
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
const requestVoiceCall = vi.fn(async () => ({ armed: true }));
vi.mock("@/lib/voiceApi", () => ({
  requestVoiceCall: () => requestVoiceCall(),
  requestVoiceHangup: async () => {},
}));
vi.mock("@/components/MascotGigi", () => ({
  MascotGigi: () => <div data-testid="mascot-gigi" />,
}));
vi.mock("@/components/layout/TopBar", () => ({
  TopBarActions: () => (
    <>
      <button type="button">Restart</button>
      <button type="button" data-testid="detach-view-button">
        Own window
      </button>
    </>
  ),
}));
vi.mock("@/components/layout/CodingModeBadge", () => ({
  CodingModeBadge: () => null,
}));
// Phase tests are not about which provider is named. Stub the display so
// this file never pulls /api/settings/voice-mode (the real hook needs a
// QueryClient). The engine-display file covers Vertex vs OpenRouter.
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

function ready() {
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
}

describe("MissionDeckView — the three acts", { timeout: 15_000 }, () => {
  beforeEach(() => {
    useDeckStore.getState().resetDeck();
    ready();
    requestVoiceCall.mockClear();
  });
  afterEach(() => cleanup());

  test("boots while the app is still coming up — no board, and nothing to answer", () => {
    useEventStore.setState({ connected: false, voiceReady: false, wsWarming: true });
    render(<MissionDeckView />);
    expect(screen.getByTestId("deck-standby")).toBeTruthy();
    expect(screen.queryByTestId("deck-board")).toBeNull();
    // No link, so nothing launches — and no screen asks to be spoken to.
    expect(screen.queryByTestId("deck-standby-cue")).toBeNull();
    expect(screen.queryByText(/Say “Hey Nova”/)).toBeNull();
  });

  test("the boot launches itself into the board, with the full hand-off", async () => {
    render(<MissionDeckView />);
    expect(screen.queryByTestId("deck-board")).toBeNull();
    // Nobody speaks, nobody clicks — the stage launches itself.
    await waitFor(() => expect(screen.getByTestId("deck-board")).toBeTruthy(), { timeout: 4000 });
    expect(useDeckStore.getState().boardOpen).toBe(true);
    // And it is the SAME launch a spoken word gets, not a hard switch.
    const standby = screen.getByTestId("deck-standby");
    await waitFor(() => expect(standby.getAttribute("data-leaving")).toBe("true"));
    expect(screen.getByTestId("deck-slot-left-top").getAttribute("data-reveal")).toBe("true");
    expect(screen.getByTestId("deck-orb-landing")).toBeTruthy();
    await waitFor(() => expect(screen.queryByTestId("deck-standby")).toBeNull(), { timeout: 4000 });
  }, 12_000);

  test("a reason for the board that comes and goes still leaves the board open", async () => {
    render(<MissionDeckView />);
    // The transport negotiates: the board takes over …
    act(() => useEventStore.setState({ voiceState: "connecting" }));
    expect(screen.getByTestId("deck-board")).toBeTruthy();
    // … and the reason vanishes without a turn. The board stays: an
    // interrupted hand-off left the ring back with nothing in it (2026-08-19).
    act(() => useEventStore.setState({ voiceState: "idle" }));
    expect(useDeckStore.getState().boardOpen).toBe(true);
    expect(screen.getByTestId("deck-board")).toBeTruthy();
    await waitFor(() => expect(screen.queryByTestId("deck-standby")).toBeNull(), { timeout: 3000 });
  });

  test("the first turn opens the board, and the board powers on around the orb", async () => {
    render(<MissionDeckView />);
    act(() => useDeckStore.getState().ingest("WakeWordDetected", { keyword: "nova" }, Date.now()));
    expect(screen.getByTestId("deck-board")).toBeTruthy();
    // Arrived from the standby on this screen: the instruments reveal, each
    // one wiping away from the orb, with the targeting frame drawn ahead.
    const left = screen.getByTestId("deck-slot-left-top");
    expect(left.getAttribute("data-reveal")).toBe("true");
    expect(left.getAttribute("data-wipe")).toBe("left");
    expect(screen.getByTestId("deck-slot-right-top").getAttribute("data-wipe")).toBe("right");
    expect(screen.getByTestId("deck-slot-centre-top").getAttribute("data-wipe")).toBe("down");
    expect(left.querySelector('[data-testid="deck-reveal-frame"]')).toBeTruthy();
    // The cards themselves mount one beat each, not in the click's own task
    // (the launch must not freeze on the board's mount): the slot is there,
    // its card is not yet, and the slot is not powered until its frame fades.
    expect(screen.queryByText("log")).toBeNull();
    expect(left.getAttribute("data-powered")).toBe("false");
    // The orb lands with a ring; the board ends with one scan.
    expect(screen.getByTestId("deck-orb-landing")).toBeTruthy();
    expect(screen.getByTestId("deck-board-sweep")).toBeTruthy();
    // The standby is still on stage for its launch — and knows it is leaving —
    // then it is gone.
    const standby = screen.getByTestId("deck-standby");
    await waitFor(() => expect(standby.getAttribute("data-leaving")).toBe("true"));
    expect(standby.querySelector('[data-testid="deck-handoff-wave"]')).toBeTruthy();
    await waitFor(() => expect(screen.getByText("log")).toBeTruthy(), { timeout: 3000 });
    await waitFor(() => expect(screen.queryByTestId("deck-standby")).toBeNull(), { timeout: 4000 });
    await waitFor(() => expect(left.getAttribute("data-powered")).toBe("true"), { timeout: 4000 });
  });

  test("'Open the board' opens it by hand and it stays open", async () => {
    render(<MissionDeckView />);
    fireEvent.click(screen.getByRole("button", { name: "Open the board" }));
    expect(screen.getByTestId("deck-board")).toBeTruthy();
    expect(useDeckStore.getState().boardOpen).toBe(true);
    // A dropped link does not send the person back to the start screen.
    act(() => useEventStore.setState({ connected: false }));
    expect(screen.getByTestId("deck-board")).toBeTruthy();
    await waitFor(() => expect(screen.queryByTestId("deck-standby")).toBeNull(), { timeout: 4000 });
  }, 10_000);

  test("pressing the orb reaches for the voice AND opens the board at once", () => {
    render(<MissionDeckView />);
    fireEvent.click(screen.getByRole("button", { name: /saying “Hey Nova”/ }));
    expect(screen.getByTestId("deck-board")).toBeTruthy();
    expect(requestVoiceCall).toHaveBeenCalledTimes(1);
  });

  test("the header carries Gigi and the chrome actions — one row, not two", () => {
    render(<MissionDeckView />);
    expect(screen.getByTestId("deck-header-gigi")).toBeTruthy();
    expect(screen.getByRole("button", { name: /^restart$/i })).toBeTruthy();
    expect(screen.getByTestId("detach-view-button")).toBeTruthy();
  });

  test("a deck that mounts into a running session is simply the board — no reveal", () => {
    useEventStore.setState({
      messages: [{ id: "m1", role: "assistant", content: "Hallo.", ts: Date.now() }],
    });
    render(<MissionDeckView />);
    expect(screen.getByTestId("deck-board")).toBeTruthy();
    expect(screen.queryByTestId("deck-standby")).toBeNull();
    const left = screen.getByTestId("deck-slot-left-top");
    expect(left.getAttribute("data-reveal")).toBe("false");
    // … cards and all, powered from the first render.
    expect(screen.getByText("log")).toBeTruthy();
    expect(left.getAttribute("data-powered")).toBe("true");
  });
});
