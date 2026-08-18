import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { LogCard } from "@/components/deck/DeckLogCard";
import { TurnCard } from "@/components/deck/DeckTurnCard";
import { CaptureCard, CAPTURE_AFTERGLOW_MS } from "@/components/deck/DeckSignalCards";
import { emptyDeckState, reduceDeck, type DeckState } from "@/lib/deckState";
import { useDeckStore } from "@/store/deck";
import { useEventStore } from "@/store/events";

/**
 * The three cards that replaced the "right now" trace, the live screen
 * mirror and the lingering capture on the front page (2026-08-18). Rendered
 * against a store fed with the same events the reducer tests use, so what
 * is asserted here is what a person sees for a real turn.
 */

function feed(events: Array<[string, unknown]>, start: DeckState = emptyDeckState(), from = 10_000): DeckState {
  let s = start;
  let ts = from;
  for (const [name, payload] of events) s = reduceDeck(s, name, payload, (ts += 250));
  return s;
}

const VOICE_TURN: Array<[string, unknown]> = [
  ["WakeWordDetected", { keyword: "nova", confidence: 0.91 }],
  ["TranscriptFinal", { transcript: { text: "wie spät ist es" } }],
  ["LatencySpan", { phase: "stt_finalize", duration_ms: 380 }],
  ["BrainTurnStarted", { provider: "openrouter", model: "claude-sonnet-5" }],
  ["LatencySpan", { phase: "brain_first_token", duration_ms: 1300 }],
  ["ActionProposed", { tool_name: "get_time" }],
  ["ActionExecuted", { tool_name: "get_time", success: true, duration_ms: 84 }],
  ["BrainTurnCompleted", { tokens_in: 1200, tokens_out: 88, cost_usd: 0.0031 }],
  ["SpeechSpoken", { text: "Es ist 14:47.", spoken_kind: "reply" }],
  ["LatencySpan", { phase: "turn_to_first_audio", duration_ms: 1900 }],
];

describe("LogCard", () => {
  beforeEach(() => {
    useDeckStore.getState().resetDeck();
    useEventStore.setState({ connected: true, voiceReady: true, wsWarming: false, voiceState: "idle" });
  });
  afterEach(() => cleanup());

  test("shows the cursor line even before anything happened", () => {
    render(<LogCard />);
    expect(screen.getByText("listening for the wake word")).toBeTruthy();
  });

  test("prints one line per thing heard, thought, done and said, with durations", () => {
    useDeckStore.setState(feed(VOICE_TURN));
    render(<LogCard />);
    expect(screen.getByText("wake word")).toBeTruthy();
    expect(screen.getByText("“wie spät ist es”")).toBeTruthy();
    expect(screen.getByText("openrouter · claude-sonnet-5")).toBeTruthy();
    expect(screen.getByText("get_time")).toBeTruthy();
    expect(screen.getByText(/84 ms/)).toBeTruthy();
    expect(screen.getByText("1.20k in · 88 out · $0.0031")).toBeTruthy();
    expect(screen.getByText("“Es ist 14:47.”")).toBeTruthy();
    expect(screen.getByText("first audio")).toBeTruthy();
    expect(screen.getByText(/1\.9 s/)).toBeTruthy();
    // The turn is over (speech spoken, brain done): the cursor is quiet again
    // — but the card is still on, with a lit "say" tag among the rows.
    expect(screen.getAllByText("say").length).toBeGreaterThan(0);
  });

  test("the title opens the sessions section", () => {
    render(<LogCard />);
    fireEvent.click(screen.getByText("Log"));
    expect(useEventStore.getState().activeSection).toBe("sessions");
  });
});

describe("TurnCard", () => {
  beforeEach(() => useDeckStore.getState().resetDeck());
  afterEach(() => cleanup());

  test("says so before the first turn", () => {
    render(<TurnCard />);
    expect(screen.getByText("No response yet.")).toBeTruthy();
  });

  test("shows the phases, the marks and the model of the last turn", () => {
    const s = feed(VOICE_TURN);
    useDeckStore.setState(feed([["SystemStateChanged", { new_state: "LISTENING", previous: "SPEAKING" }]], s, 20_000));
    render(<TurnCard />);
    expect(screen.getByText("#1 · last")).toBeTruthy();
    for (const ph of ["hear", "think", "act", "speak"]) expect(screen.getByText(ph)).toBeTruthy();
    expect(screen.getByText("380 ms")).toBeTruthy(); // transcript
    expect(screen.getByText("1.3 s")).toBeTruthy(); // first token
    expect(screen.getByText("1.9 s")).toBeTruthy(); // first audio
    expect(screen.getByText(/claude-sonnet-5/)).toBeTruthy();
    expect(screen.getByText(/1 tools/)).toBeTruthy();
    expect(screen.getByText(/4 words/)).toBeTruthy();
  });

  test("runs live while a turn is open", () => {
    // Fed at the real clock: a turn nobody touched for 90 s counts as quiet.
    useDeckStore.setState(
      feed(
        [
          ["TranscriptFinal", { transcript: { text: "hallo" } }],
          ["BrainTurnStarted", { provider: "openrouter", model: "m" }],
        ],
        emptyDeckState(),
        Date.now() - 1_000,
      ),
    );
    render(<TurnCard />);
    expect(screen.getByText("#1 · live")).toBeTruthy();
    expect(screen.getByText("after you finished speaking")).toBeTruthy();
  });
});

describe("CaptureCard", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useDeckStore.getState().resetDeck();
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  test("shows a fresh capture with a fading rail, then only the ledger", () => {
    useDeckStore.setState(
      feed([["ScreenCaptureCompleted", { target_label: "Chrome", width: 1920, height: 1080, redaction_count: 2 }]]),
    );
    render(<CaptureCard />);
    // Up: the picture, its label, what was redacted, and the countdown.
    expect(document.querySelector("img")).toBeTruthy();
    expect(screen.getByText("Chrome")).toBeTruthy();
    expect(screen.getByText("2 redacted")).toBeTruthy();
    expect(screen.getByText(/fades in/)).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(CAPTURE_AFTERGLOW_MS + 500);
    });
    // Gone: no picture lingers, the ledger remembers the look in words.
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText("Earlier this session")).toBeTruthy();
    expect(screen.getByText("Chrome")).toBeTruthy();
    expect(screen.getByText("1920×1080")).toBeTruthy();
  });

  test("says so when nothing was captured", () => {
    render(<CaptureCard />);
    expect(screen.getByText("No capture right now.")).toBeTruthy();
  });
});
