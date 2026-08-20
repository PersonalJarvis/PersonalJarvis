import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { DeckStandby } from "@/components/deck/DeckStandby";
import type { WakeWordConfig } from "@/hooks/useWakeWord";
import { useDeckStore } from "@/store/deck";
import { useEventStore } from "@/store/events";

/**
 * The deck before the board (2026-08-18): a boot console that lights the
 * four gates as they turn true, on a ring that sweeps once the wake word is
 * really being listened for — with the board one press away. Rendered
 * against the real stores so what is asserted is what a person sees while
 * the app comes up.
 *
 * There is no waiting screen any more (maintainer, 2026-08-20): the stage
 * never asks to be spoken to, it launches into the board on its own
 * (`lib/deckStandby.ts::AUTO_LAUNCH`, driven by MissionDeckView).
 */

const WAKE: WakeWordConfig = {
  phrase: "Hey Nova",
  engine: "openwakeword",
  custom_model_path: "",
  fuzzy_match_ratio: 0.8,
  language: "auto",
  engines: ["openwakeword"],
  instant_phrases: [],
  local_whisper_available: false,
  enabled: true,
};

const READOUTS = { nw: "ready", ne: "0 steps", sw: "—", se: "0 words" };

function renderStage(extra: Partial<Parameters<typeof DeckStandby>[0]> = {}) {
  const onOpenBoard = vi.fn();
  const onPressOrb = vi.fn();
  const utils = render(
    <DeckStandby
      steps={[]}
      busy={false}
      readouts={READOUTS}
      wakeConfig={WAKE}
      onPressOrb={onPressOrb}
      pressLabel="Start talking"
      pressDisabled={false}
      onOpenBoard={onOpenBoard}
      {...extra}
    />,
  );
  return { ...utils, onOpenBoard, onPressOrb };
}

function console_() {
  return within(screen.getByTestId("deck-boot-console"));
}

describe("DeckStandby — the app coming up", () => {
  beforeEach(() => {
    useDeckStore.getState().resetDeck();
    useEventStore.setState({
      connected: false,
      voiceReady: false,
      wsWarming: true,
      voiceState: "idle",
      brainProvider: "",
      brainModel: "",
      assistantName: "Nova",
      activeSection: "chats",
    });
  });
  afterEach(() => cleanup());

  test("names the act, the assistant, and waits on the link first", () => {
    renderStage();
    expect(screen.getByText("boot sequence")).toBeTruthy();
    expect(screen.getByTestId("deck-boot-title").getAttribute("aria-label")).toBe("Nova starting");
    // The console: the first gate only, pending — later lines wait their turn.
    const c = console_();
    expect(c.getByText("connecting to the assistant")).toBeTruthy();
    expect(c.queryByText("voice stack starting")).toBeNull();
    // The stage never asks to be spoken to; the board is one press away.
    expect(screen.queryByText(/Say “Hey Nova”/)).toBeNull();
    expect(screen.queryByTestId("deck-standby-cue")).toBeNull();
    expect(screen.getByRole("button", { name: "Open the board" })).toBeTruthy();
  });

  test("lights the gates in order as they turn true, and the arcs follow", () => {
    const { rerender } = renderStage();
    act(() => useEventStore.setState({ connected: true }));
    let c = console_();
    expect(c.getByText("connected")).toBeTruthy();
    expect(c.getByText("voice stack starting")).toBeTruthy();
    expect(c.queryByText("reading the brain")).toBeNull();

    act(() => useEventStore.setState({ voiceReady: true, brainProvider: "openrouter", brainModel: "claude-sonnet-5" }));
    rerender(
      <DeckStandby
        steps={[]}
        busy={false}
        readouts={READOUTS}
        wakeConfig={WAKE}
        onPressOrb={() => {}}
        pressLabel="Start talking"
        pressDisabled={false}
        onOpenBoard={() => {}}
      />,
    );
    c = console_();
    expect(c.getByText("voice ready")).toBeTruthy();
    expect(c.getByText("openrouter · claude-sonnet-5")).toBeTruthy();
    expect(c.getByText("listening for “Hey Nova”")).toBeTruthy();

    const ring = screen.getByTestId("deck-standby-ring");
    const states = Array.from(ring.querySelectorAll(".deck-gate-arc")).map(
      (el) => `${el.getAttribute("data-gate")}:${el.getAttribute("data-state")}`,
    );
    expect(states).toEqual(["link:ok", "voice:ok", "brain:ok", "wake:ok"]);
    // Every gate was pending when the stage mounted: all four draw in.
    expect(ring.querySelectorAll('.deck-gate-arc[data-fresh="true"]')).toHaveLength(4);
  });

  test("the orb press and the board button reach the parent", () => {
    act(() => useEventStore.setState({ connected: true }));
    const { onOpenBoard, onPressOrb } = renderStage();
    fireEvent.click(screen.getByRole("button", { name: "Open the board" }));
    expect(onOpenBoard).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Start talking" }));
    expect(onPressOrb).toHaveBeenCalledTimes(1);
  });
});

describe("DeckStandby — every gate up", () => {
  beforeEach(() => {
    useDeckStore.getState().resetDeck();
    useEventStore.setState({
      connected: true,
      voiceReady: true,
      wsWarming: false,
      voiceState: "idle",
      brainProvider: "groq",
      brainModel: "",
      assistantName: "Nova",
      activeSection: "chats",
    });
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  test("listens on the ring and shows the four gates standing — and asks for nothing", () => {
    renderStage();
    // The act is the boot to the last frame: no standby, no cue, no line
    // telling anybody to speak (cut 2026-08-20).
    expect(screen.getByText("boot sequence")).toBeTruthy();
    expect(screen.queryByText("standby")).toBeNull();
    expect(screen.queryByTestId("deck-standby-cue")).toBeNull();
    expect(screen.queryByText(/The board opens the moment you speak/)).toBeNull();
    // The wake word is on and the voice is idle: the ring sweeps.
    expect(screen.getByTestId("deck-standby-ring").getAttribute("data-sweep")).toBe("true");
    expect(screen.getByTestId("deck-ring-sweep")).toBeTruthy();
    // Nothing was pending when the stage mounted: no arc draws in, no time is claimed.
    expect(document.querySelectorAll('.deck-gate-arc[data-fresh="true"]')).toHaveLength(0);
    const c = console_();
    expect(c.getByText("connected")).toBeTruthy();
    expect(c.getByText("groq")).toBeTruthy();
    expect(c.getByText("listening for “Hey Nova”")).toBeTruthy();
    expect(c.queryByText(/ ms$| s$/)).toBeNull();
    // The console ends with the gates. The standby's live cursor line — the
    // one that sat there counting how long you had been quiet — is gone.
    expect(c.queryByText("listening for the wake word")).toBeNull();
  });

  test("a wake word that is off is said so, and the ring stays still", () => {
    renderStage({ wakeConfig: { ...WAKE, enabled: false } });
    expect(console_().getByText(/wake word off/)).toBeTruthy();
    expect(screen.getByTestId("deck-standby-ring").getAttribute("data-sweep")).toBe("false");
    expect(screen.queryByTestId("deck-ring-sweep")).toBeNull();
  });

  test("a brain that never shows up is called absent once the dust settles, and the line opens the keys", () => {
    vi.useFakeTimers();
    useEventStore.setState({ brainProvider: "" });
    renderStage();
    let c = console_();
    expect(c.getByText("reading the brain")).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(4_000);
    });
    c = console_();
    const line = c.getByRole("button", { name: /no brain configured/ });
    fireEvent.click(line);
    expect(useEventStore.getState().activeSection).toBe("apikeys");
    // The wake gate settled too — the console reached its last line.
    expect(c.getByText("listening for “Hey Nova”")).toBeTruthy();
  });

  test("keeps the sweep off while the voice reports trouble", () => {
    useEventStore.setState({ voiceState: "error" });
    renderStage();
    expect(screen.getByTestId("deck-standby-ring").getAttribute("data-sweep")).toBe("false");
  });
});
