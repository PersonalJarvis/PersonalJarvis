import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

// framer-motion is real here; only the reduced-motion hook is pinned, so the
// voice loops do not run during the assertions.
vi.mock("framer-motion", async () => {
  const actual = await vi.importActual<typeof import("framer-motion")>("framer-motion");
  return {
    ...actual,
    useReducedMotion: () => true,
  };
});

import { DeckOrb } from "@/components/deck/DeckOrb";
import { useEventStore } from "@/store/events";
import { clearVoiceInputLevel, setVoiceInputLevel } from "@/lib/voiceInputLevel";
import type { ThinkingStep } from "@/lib/thinkingSteps";

/**
 * The orb is the click-shaped wake word (maintainer, 2026-08-18): pressing
 * the orb in the centre does what saying the phrase does. Display-only
 * callers get no button at all. The centre is the MASCOT — vectors, no
 * bitmap: the `/deck-orb.png` render that used to sit there was a picture of
 * a light whose baked-in white halo read as a grey box on the dark deck, and
 * the maintainer had asked repeatedly for it to go (2026-08-20). It carries
 * the live voice state so it can breathe with it.
 */
describe("DeckOrb", () => {
  afterEach(() => cleanup());

  test("is display only without a press handler", () => {
    render(<DeckOrb steps={[]} busy={false} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByTestId("jarvis-orb")).toBeTruthy();
  });

  test("the reticle is instruments only — no scenery left to turn", () => {
    const { container, rerender } = render(<DeckOrb steps={[]} busy={false} />);
    // Gone with the PNG: dial ticks, corner brackets, the two orbits, the
    // satellite, the idle ping. Instruments that measure nothing are a stage
    // set, and the living thing in the middle carries the life instead.
    expect(container.querySelector(".deck-orb-orbit-a")).toBeNull();
    expect(container.querySelector(".deck-orb-orbit-b")).toBeNull();
    expect(container.querySelector(".deck-orb-rings")).toBeNull();
    expect(screen.queryByTestId("deck-orb-satellite")).toBeNull();
    expect(screen.queryByTestId("deck-orb-ping")).toBeNull();
    // The 72 dial ticks and the compass crosshairs were the only <line>s here.
    expect(container.querySelectorAll("line")).toHaveLength(0);
    // What stays is what reads a real value: the stage light the voice moves
    // (it carried a level arc on the bezel until 2026-08-21), the ripple host
    // — at rest at zero — and one arc per running step.
    expect(screen.getByTestId("deck-orb-vu")).toBeTruthy();
    expect(screen.getByTestId("deck-orb-ripples").childElementCount).toBe(0);
    expect(screen.getByTestId("deck-orb").style.getPropertyValue("--orb-level")).toBe("0");
    const step: ThinkingStep = {
      id: "a",
      kind: "tool",
      labelKey: "thinking.tool",
      status: "active",
      startedTs: 0,
    };
    rerender(<DeckOrb steps={[step]} busy />);
    expect(container.querySelectorAll("path").length).toBeGreaterThan(0);
  });

  test("moves with the voice: the real microphone level while listening, one variable for all", async () => {
    useEventStore.setState({ voiceState: "listening" });
    setVoiceInputLevel(0.8, "native");
    try {
      render(<DeckOrb steps={[]} busy={false} />);
      const root = screen.getByTestId("deck-orb");
      await waitFor(() => {
        expect(Number(root.style.getPropertyValue("--orb-level"))).toBeGreaterThan(0.3);
      });
      // A word landing sent a ripple from the sun towards the bezel.
      expect(screen.getByTestId("deck-orb-ripples").childElementCount).toBeGreaterThan(0);
    } finally {
      clearVoiceInputLevel();
      useEventStore.setState({ voiceState: "idle" });
    }
  });

  test("the centre is the mascot itself, drawn as vectors — no bitmap left", () => {
    render(<DeckOrb steps={[]} busy={false} />);
    const orb = screen.getByTestId("jarvis-orb");
    expect(orb.getAttribute("data-voice")).toBe("idle");
    // The PNG is gone from the tree, so it cannot come back in through here.
    expect(orb.querySelectorAll("img")).toHaveLength(0);
    expect(orb.querySelector(".gigi-root")).toBeTruthy();
    expect(orb.querySelector("svg")).toBeTruthy();
  });

  test("a press on the orb fires the handler and carries its label", () => {
    const onPress = vi.fn();
    render(
      <DeckOrb
        steps={[]}
        busy={false}
        onPress={onPress}
        pressLabel="Start talking — the same as saying “Hey Nova”."
      />,
    );
    const button = screen.getByRole("button", { name: /saying “Hey Nova”/ });
    expect(button.getAttribute("title")).toContain("Hey Nova");
    fireEvent.click(screen.getByTestId("jarvis-orb"));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  test("stays inert while the call is being set up", () => {
    const onPress = vi.fn();
    render(
      <DeckOrb steps={[]} busy={false} onPress={onPress} pressLabel="Start" pressDisabled />,
    );
    const button = screen.getByRole("button", { name: "Start" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(onPress).not.toHaveBeenCalled();
  });
});
