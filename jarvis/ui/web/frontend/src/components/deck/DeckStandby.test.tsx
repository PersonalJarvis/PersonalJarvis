import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { DeckStandby } from "@/components/deck/DeckStandby";
import type { WakeWordConfig } from "@/hooks/useWakeWord";
import { useDeckStore } from "@/store/deck";
import { useEventStore } from "@/store/events";

/**
 * The deck before the first word (2026-08-18): a boot console that lights
 * the four gates as they turn true, then a listening ring — with the board
 * one press away. Rendered against the real stores so what is asserted is
 * what a person sees while the app comes up.
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

function renderStage(
  phase: "boot" | "standby",
  extra: Partial<Parameters<typeof DeckStandby>[0]> = {},
) {
  const onOpenBoard = vi.fn();
  const onPressOrb = vi.fn();
  const utils = render(
    <DeckStandby
      phase={phase}
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

describe("DeckStandby — boot", () => {
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
    renderStage("boot");
    expect(screen.getByTestId("deck-standby").getAttribute("data-phase")).toBe("boot");
    expect(screen.getByText("boot sequence")).toBeTruthy();
    expect(screen.getByTestId("deck-boot-title").getAttribute("aria-label")).toBe("Nova starting");
    // The console: the first gate only, pending — later lines wait their turn.
    const c = console_();
    expect(c.getByText("connecting to the assistant")).toBeTruthy();
    expect(c.queryByText("voice stack starting")).toBeNull();
    // No board yet, so no idle headline; but the board is one press away.
    expect(screen.queryByText(/Say “Hey Nova”/)).toBeNull();
    expect(screen.getByRole("button", { name: "Open the board" })).toBeTruthy();
  });

  test("lights the gates in order as they turn true, and the arcs follow", () => {
    const { rerender } = renderStage("boot");
    act(() => useEventStore.setState({ connected: true }));
    let c = console_();
    expect(c.getByText("connected")).toBeTruthy();
    expect(c.getByText("voice stack starting")).toBeTruthy();
    expect(c.queryByText("reading the brain")).toBeNull();

    act(() => useEventStore.setState({ voiceReady: true, brainProvider: "openrouter", brainModel: "claude-sonnet-5" }));
    rerender(
      <DeckStandby
        phase="standby"
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
    const { onOpenBoard, onPressOrb } = renderStage("boot");
    fireEvent.click(screen.getByRole("button", { name: "Open the board" }));
    expect(onOpenBoard).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Start talking" }));
    expect(onPressOrb).toHaveBeenCalledTimes(1);
  });
});

describe("DeckStandby — standby", () => {
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

  test("names the phrase to say, listens on the ring, and shows the four gates standing", () => {
    renderStage("standby");
    expect(screen.getByTestId("deck-standby").getAttribute("data-phase")).toBe("standby");
    expect(screen.getByText("standby")).toBeTruthy();
    expect(screen.getByText("Say “Hey Nova” — or click the orb.")).toBeTruthy();
    // The wake word is on and the voice is idle: the ring sweeps.
    expect(screen.getByTestId("deck-standby-ring").getAttribute("data-sweep")).toBe("true");
    expect(screen.getByTestId("deck-ring-sweep")).toBeTruthy();
    // Nothing was pending when the stage mounted: no arc draws in, no time is claimed.
    expect(document.querySelectorAll('.deck-gate-arc[data-fresh="true"]')).toHaveLength(0);
    const c = console_();
    expect(c.getByText("connected")).toBeTruthy();
    expect(c.getByText("groq")).toBeTruthy();
    expect(c.getByText("listening for the wake word")).toBeTruthy();
    expect(c.queryByText(/ ms$| s$/)).toBeNull();
  });

  test("a wake word that is off is said so, and the ring stays still", () => {
    renderStage("standby", { wakeConfig: { ...WAKE, enabled: false } });
    expect(console_().getByText(/wake word off/)).toBeTruthy();
    expect(screen.getByTestId("deck-standby-ring").getAttribute("data-sweep")).toBe("false");
    expect(screen.queryByTestId("deck-ring-sweep")).toBeNull();
    expect(console_().getByText("voice idle")).toBeTruthy();
  });

  test("a brain that never shows up is called absent once the dust settles, and the line opens the keys", () => {
    vi.useFakeTimers();
    useEventStore.setState({ brainProvider: "" });
    renderStage("standby");
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
    renderStage("standby");
    expect(screen.getByTestId("deck-standby-ring").getAttribute("data-sweep")).toBe("false");
    expect(console_().getByText(/voice reported an error/)).toBeTruthy();
  });
});
